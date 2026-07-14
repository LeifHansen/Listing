// Thin fetch wrapper shared by every API call. Errors surface as friendly
// messages the UI can toast. Every request carries a timeout — a fetch with
// no timeout can hang forever and freeze whatever overlay is waiting on it.
const DEFAULT_TIMEOUT_MS = 60_000;

export async function api(path, opts = {}) {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...fetchOpts } = opts;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(path, { ...fetchOpts, signal: ctrl.signal });
  } catch (e) {
    if (e.name === "AbortError") {
      const err = new Error("The server is taking unusually long to respond.");
      err.timedOut = true;
      throw err;
    }
    throw new Error("Network error — the server may be starting up. Try again in a few seconds.");
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch (e) { /* non-JSON error body */ }
    throw new Error(`(${res.status}) ${detail}`);
  }
  return res.json();
}

export function postJson(path, body, opts = {}) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...opts,
  });
}

// Phone photos are often 5-12MB; the server only needs ~1600px. Re-encoding
// in the browser before upload cuts transfer time ~10x. Formats the browser
// can't decode (e.g. HEIC) fall through and upload as-is — the server
// handles them.
const MAX_UPLOAD_SIDE = 2000;
export async function downscaleForUpload(file) {
  try {
    let bmp;
    try {
      bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch (e) {
      bmp = await createImageBitmap(file);
    }
    const scale = Math.min(1, MAX_UPLOAD_SIDE / Math.max(bmp.width, bmp.height));
    if (scale >= 1 && file.size < 2 * 1024 * 1024) { bmp.close(); return file; }
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bmp.width * scale));
    canvas.height = Math.max(1, Math.round(bmp.height * scale));
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    bmp.close();
    const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.9));
    if (!blob) return file;
    const name = (file.name || "photo").replace(/\.\w+$/, "") + ".jpg";
    return new File([blob], name, { type: "image/jpeg" });
  } catch (e) {
    return file; // never block the upload on a client-side optimization
  }
}

// HEIC/HEIF often arrive with an empty MIME type (Chrome, Windows), so accept
// by extension too — the server decodes anything Pillow can.
export const IMAGE_EXT_RE = /\.(jpe?g|png|webp|bmp|gif|tiff?|heic|heif|hif)$/i;

// Sample up to `maxFrames` evenly-spaced JPEG frames from a recorded video,
// scaled down so the upload stays small. Runs entirely in the browser.
export async function extractFrames(file, maxFrames = 6) {
  const url = URL.createObjectURL(file);
  const video = document.createElement("video");
  video.muted = true; video.playsInline = true; video.preload = "auto"; video.src = url;
  try {
    await new Promise((res, rej) => {
      video.onloadedmetadata = () => res();
      video.onerror = () => rej(new Error("Couldn't read that video."));
    });
    const dur = (isFinite(video.duration) && video.duration > 0) ? video.duration : 0;
    const vw = video.videoWidth || 640, vh = video.videoHeight || 480;
    const scale = Math.min(1, 1024 / Math.max(vw, vh));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(vw * scale));
    canvas.height = Math.max(1, Math.round(vh * scale));
    const ctx = canvas.getContext("2d");
    const grab = (t) => new Promise((res) => {
      let done = false;
      const finish = (b) => {
        if (!done) { done = true; video.removeEventListener("seeked", onSeeked); res(b); }
      };
      const onSeeked = () => {
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob((b) => finish(b), "image/jpeg", 0.85);
      };
      video.addEventListener("seeked", onSeeked);
      setTimeout(() => finish(null), 4000); // don't hang if 'seeked' never fires
      video.currentTime = t;
    });
    const frames = [];
    if (dur) {
      const n = Math.max(1, maxFrames);
      for (let i = 0; i < n; i++) {
        const t = Math.min(dur - 0.05, (dur * (i + 0.5)) / n);
        const b = await grab(Math.max(0, t));
        if (b) frames.push(b);
      }
    } else {
      const b = await grab(0);
      if (b) frames.push(b);
    }
    return frames;
  } finally {
    URL.revokeObjectURL(url);
  }
}

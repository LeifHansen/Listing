// Thin fetch wrapper shared by every API call. Errors surface as friendly
// messages the UI can toast.
export async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error("Network error — the server may be starting up. Try again in a few seconds.");
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

export function postJson(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// Poll a background job (see /api/bulk/status) until it finishes, then resolve
// with its `result` (or throw its `error`). Used so slow AI steps run as a job
// the client polls instead of a single long request that a gateway or the
// browser would time out ("server taking too long to respond"). If we give up
// waiting, the server keeps working and saves the draft anyway.
export async function pollJob(jobId, { intervalMs = 1500, timeoutMs = 240000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const j = await api(`/api/bulk/status/${jobId}`);
    if (j.done) {
      if (j.error) throw new Error(j.error);
      return j.result;
    }
    if (Date.now() > deadline) {
      throw new Error(
        "The AI is taking longer than usual. Your photos are still here — "
        + "tap Identify to try again, or check Drafts in a moment; it may have finished.",
      );
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
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

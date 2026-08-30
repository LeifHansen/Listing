import { API_BASE, apiUrl, storedToken, tokenReady } from "@/lib/platform";

// Kick off an OAuth connect flow (eBay/Etsy/Depop). On the web it's a plain
// same-origin navigation, exactly as before. In the native shell the
// navigation is cross-origin and carries neither the Bearer header nor a
// cookie, so it first mints a 60-second single-purpose ticket and rides that;
// native=1 tells the callback to steer the webview back into the app.
export async function startConnect(path) {
  if (!API_BASE) {
    window.location.href = path;
    return;
  }
  const { ticket } = await postJson("/api/auth/connect-ticket", {});
  window.location.href = apiUrl(
    `${path}?ticket=${encodeURIComponent(ticket)}&native=1`);
}

// Paths that transmit the user's photos onward to the AI provider. Apple's
// guideline 5.1.2(i) (and plain courtesy) requires explicit consent before
// the FIRST such transmission, so these calls gate on ensureAiConsent().
// One choke point here beats a check in every upload flow — new flows are
// covered automatically.
const AI_PHOTO_RE = /^\/api\/(upload|upload-more|bulk\/upload|shelf-scan|identify)/;

const AI_CONSENT_KEY = "thryft-ai-consent";

export function hasAiConsent() {
  try { return localStorage.getItem(AI_CONSENT_KEY) === "yes"; } catch (e) { return true; }
}

export function grantAiConsent() {
  try { localStorage.setItem(AI_CONSENT_KEY, "yes"); } catch (e) {}
}

// Resolves once the user has agreed (now, or previously); rejects if they
// decline. The dialog itself lives in the app shell — this just raises the
// "ai-consent:needed" event and waits for its verdict.
function ensureAiConsent() {
  if (hasAiConsent()) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const detail = {
      accept: () => { grantAiConsent(); resolve(); },
      decline: () => reject(new Error(
        "Photos aren't analyzed without your OK — you can agree any time.")),
    };
    try {
      window.dispatchEvent(new CustomEvent("ai-consent:needed", { detail }));
    } catch (e) {
      resolve(); // no listener/some exotic browser — never brick the app
    }
  });
}

// How long any one request may take before the client stops waiting. Photo
// work on a shared machine is the slow case: an inference queued behind a
// bulk batch, plus the upload itself. Callers with longer work (uploads,
// bulk) pass their own opts.timeoutMs, and 0 disables it.
const DEFAULT_TIMEOUT_MS = 90000;
// Uploads move real bytes: a bulk batch over a phone connection takes
// minutes and is not stuck.
export const UPLOAD_TIMEOUT_MS = 300000;
// Calls that wait on the background-removal model. The default above is a
// NETWORK deadline and far too short for these: one isnet inference has been
// measured at 104s on the production machine (shared CPU, one inference thread
// by design so the app can keep answering health checks), and the request also
// queues for the single-flight inference lock before that. At 90s the studio's
// "Remove background" gave up while its own answer was still being computed —
// the seller saw a failure, the server finished into a closed socket, and the
// photo they then saved was the untouched original. ONNX cannot be interrupted
// mid-run, so the client has to outlast it.
export const MODEL_TIMEOUT_MS = 240000;

// A deadline for a request that runs the cutout model over SEVERAL photos in
// one go. Inference is single-flight on the server, so N photos cost roughly N
// inferences end to end — a flat UPLOAD_TIMEOUT_MS meant "Add photos" with
// background removal on gave up part-way through work the server was still
// doing, every time, for anything past two photos. The cap keeps a genuinely
// stuck request from hanging the UI forever.
const BATCH_MODEL_CAP_MS = 900000;   // 15 min
export function batchModelTimeoutMs(count, removeBg) {
  if (!removeBg) return UPLOAD_TIMEOUT_MS;
  return Math.min(BATCH_MODEL_CAP_MS,
                  UPLOAD_TIMEOUT_MS + Math.max(0, count) * MODEL_TIMEOUT_MS);
}

// Thin fetch wrapper shared by every API call. Errors surface as friendly
// messages the UI can toast.
// Methods that are safe to repeat. A timeout on one of these tells the seller
// nothing was lost, because repeating it cannot do the work twice. Anything
// else may already have changed something on a marketplace.
//
// A missing method is a GET (fetch's own default), which is why the default
// is the safe side rather than the cautious one.
const REPEATABLE = new Set(["GET", "HEAD", "OPTIONS"]);

export function isRepeatable(method) {
  return REPEATABLE.has(String(method || "GET").toUpperCase());
}

export async function api(path, opts = {}) {
  if (AI_PHOTO_RE.test(path)) await ensureAiConsent();
  // Native shell: same-origin cookies never travel, so authenticate with the
  // stored bearer token instead (no-op on the web build). tokenReady settles
  // once — reading the Keychain is asynchronous, and without waiting for it
  // the first call after a cold start would go out unauthenticated.
  await tokenReady();
  const token = storedToken();
  if (token && !(opts.headers && opts.headers.Authorization)) {
    opts = { ...opts, headers: { ...(opts.headers || {}), Authorization: `Bearer ${token}` } };
  }
  // Every request gets a deadline. Without one a stalled connection leaves a
  // spinner up forever, and the seller's only signal is that nothing is
  // happening — indistinguishable from slow, from broken, and from a tap that
  // never registered. A caller with genuinely long work passes its own
  // timeoutMs (or 0 to opt out); the default is generous enough for an image
  // round trip and short enough to still be an answer.
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const abort = timeoutMs > 0 ? new AbortController() : null;
  const timer = abort ? setTimeout(() => abort.abort(), timeoutMs) : null;
  // A caller's own signal used to be overwritten by ours and silently stopped
  // working. Forward it instead, so `signal` means what it says wherever it is
  // passed -- the trap that made studioCall's 240s deadline a no-op.
  if (abort && opts.signal) {
    if (opts.signal.aborted) abort.abort();
    else opts.signal.addEventListener("abort", () => abort.abort(), { once: true });
  }
  let res;
  try {
    res = await fetch(apiUrl(path), abort ? { ...opts, signal: abort.signal } : opts);
  } catch (e) {
    if (e?.name === "AbortError") {
      // What a timeout MEANS depends on the method, and saying the wrong
      // thing here is worse than saying nothing.
      //
      // Giving up on a request is not the same as the server giving up on
      // it. A read can be repeated freely, so "nothing was lost" is true. A
      // publish, a promotion, a policy creation or a delete may already have
      // reached eBay and succeeded — the response is what got lost, not the
      // work. Telling that seller "nothing was lost, try again" invites them
      // to create a second live listing, and this message used to be sent on
      // every timeout regardless of method.
      const seconds = Math.round(timeoutMs / 1000);
      throw new Error(
        isRepeatable(opts.method)
          ? `That took longer than ${seconds}s and was given up on. `
            + "Nothing was lost — try again."
          : `That took longer than ${seconds}s, so we stopped waiting — but it `
            + "may still have gone through. Check before trying again, so you "
            + "don't end up doing it twice.",
        { cause: e });
    }
    throw new Error(
      "Network error — the server may be starting up. Try again in a few seconds.",
      { cause: e });
  } finally {
    if (timer) clearTimeout(timer);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch (e) { /* non-JSON error body */ }
    // Out of AI tokens (402 mentioning tokens — distinct from the unrelated
    // 402 the server maps Anthropic-credit exhaustion to): let the app shell
    // open the buy-tokens dialog on top of whatever toast the caller shows.
    if (res.status === 402 && /token/i.test(String(detail))) {
      try { window.dispatchEvent(new CustomEvent("tokens:needed", { detail })); } catch (e) {}
    }
    const err = new Error(`(${res.status}) ${detail}`);
    err.status = res.status;
    throw err;
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
export async function pollJob(jobId, { intervalMs = 1500, timeoutMs = 240000, onUpdate } = {}) {
  // The timeout is per STAGE, not per job: the server heartbeats each phase
  // change (optimizing -> identifying -> category -> specifics -> maker), and
  // the deadline resets whenever the job visibly advances. A job is only
  // declared stuck after timeoutMs with NO progress — a legitimately long
  // multi-stage chain used to blow a fixed 240s budget while the server was
  // still working. `onUpdate(status)` (optional) sees every raw status so
  // callers can render live stage progress.
  let deadline = Date.now() + timeoutMs;
  let lastSeen = "";
  for (;;) {
    const j = await api(`/api/bulk/status/${jobId}`);
    if (onUpdate) {
      try { onUpdate(j); } catch { /* display-only */ }
    }
    if (j.done) {
      if (j.error) throw new Error(j.error);
      return j.result;
    }
    const seen = `${j.phase || ""}|${j.beat || ""}|${j.current || ""}`;
    if (seen !== lastSeen) {
      lastSeen = seen;
      deadline = Date.now() + timeoutMs;
    }
    if (Date.now() > deadline) {
      throw new Error(
        "The AI is taking longer than usual. Your photos are still here — "
        + "tap Identify with AI to try again, or check Drafts in a moment; it may have finished.",
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
      // Some browsers reject the options bag. Decode via <img> instead — that
      // path always applies EXIF orientation when drawn to a canvas. A bare
      // createImageBitmap(file) here would NOT, and since this function
      // re-encodes (dropping EXIF), it painted phone photos sideways.
      bmp = await new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const el = new Image();
        el.onload = () => { URL.revokeObjectURL(url); resolve(el); };
        el.onerror = () => { URL.revokeObjectURL(url); reject(new Error("decode failed")); };
        el.src = url;
      });
    }
    const w = bmp.naturalWidth || bmp.width;
    const h = bmp.naturalHeight || bmp.height;
    const scale = Math.min(1, MAX_UPLOAD_SIDE / Math.max(w, h));
    if (scale >= 1 && file.size < 2 * 1024 * 1024) { bmp.close?.(); return file; }
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(w * scale));
    canvas.height = Math.max(1, Math.round(h * scale));
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    bmp.close?.();
    const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.9));
    if (!blob) return file;
    const name = (file.name || "photo").replace(/\.\w+$/, "") + ".jpg";
    return new File([blob], name, { type: "image/jpeg" });
  } catch (e) {
    return file; // never block the upload on a client-side optimization
  }
}

// Downscale a whole pile before upload, a few photos at a time. Decoding is
// the expensive part — each in-flight photo holds a full-resolution bitmap —
// so kicking off 250 at once (a full bulk batch) freezes the tab and can
// crash it on phones. Order is preserved.
export async function downscaleAllForUpload(files, limit = 4) {
  const out = new Array(files.length);
  let next = 0;
  const worker = async () => {
    for (;;) {
      const i = next++;
      if (i >= files.length) return;
      out[i] = await downscaleForUpload(files[i]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, files.length) }, worker));
  return out;
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

/* BackgroundRemovalService: the one thing the app calls to take a background
   off a photo.

   The work happens ON THE SELLER'S DEVICE wherever it can -- Apple Vision on
   an iPhone, ML Kit on Android, a BiRefNet model in the browser -- for free,
   with nothing sent anywhere. Every engine answers the same thing: a soft
   greyscale MATTE of the photo it was handed. The server turns that into
   the listing photo with the compositor every cutout goes through, so the
   result looks the same whichever engine made it. Photoroom stays as the
   fallback: automatically only for a seller who switched cloud removal on
   (and has tokens and the app has budget), and never more than once per
   photo.

   The engines share one interface (JSDoc, as this codebase has no
   TypeScript):

     BackgroundRemovalProvider {
       id: string, platform: "ios"|"android"|"web"|"server",
       isSupported(): Promise<{supported, ready, reason}>,
       initialize({onProgress}?): Promise<{supported, ready, reason}>,
       removeBackground(image: Blob, opts?): Promise<RemovalResult>,
     }
     RemovalResult = {mask: Blob, width, height}        // the device engines
                   | {image: string}                     // Photoroom, preview
                   | {saved: true, version, updated_at}  // Photoroom, saved

   Which engine may run is the server's to say (/api/bg-removal/status
   `local`, read on every start), so one that misbehaves in the field is
   switched off without an app release. Capability is detected from the
   device itself -- the plugin's own answer, WebGPU's real adapter limits --
   never from the name of the browser or phone.

   What this module guarantees:
     * one attempt per engine per photo, and at most one hop to the cloud --
       no loops, no retries past the one re-read of a photo that changed;
     * one request at a time per photo (in-flight dedupe), and a repeat of
       the same bytes answered from a small cache rather than cut again;
     * a deadline on every device cutout, and an AbortSignal that stops
       waiting at once;
     * nothing reaches Photoroom unless `allowCloud` says the seller agreed. */
import { api } from "@/lib/api";
import { fetchPhoto } from "@/lib/photoFile";
import { nativePlatform } from "@/lib/platform";
import { LocalCutoutError } from "./errors";
import { AppleVisionProvider, GoogleMLKitProvider } from "./providers/native";
import { PhotoroomProvider } from "./providers/photoroom";

const DEFAULT_TIMEOUT_MS = 45000;
// The coverage under which a matte "found nothing" -- the server's own
// threshold (images._MIN_FG_COVERAGE), checked here only to skip a pointless
// upload; the server checks it again and is the one that decides.
const MIN_COVERAGE = 0.005;
const CACHE_SIZE = 12;

// --- configuration and capability, shared by every screen ----------------------

let config = null;          // status.local from the server, or null (= off)
let snapshot = { status: "off", provider: null, reason: "", progress: null };
let checking = null;
const listeners = new Set();

function emit(next) {
  snapshot = { ...snapshot, ...next };
  for (const fn of listeners) {
    try { fn(); } catch (e) { /* a listener never breaks the rest */ }
  }
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getSnapshot() {
  return snapshot;
}

/** What the server says the device may do. Called by the store with
    /api/bg-removal/status `local` every time it is read. */
export function configureLocal(local) {
  const before = JSON.stringify(config?.providers || []);
  config = local && Array.isArray(local.providers) ? local : null;
  if (JSON.stringify(config?.providers || []) !== before) {
    checking = null;
    emit({ status: config ? "unknown" : "off", provider: null, reason: "",
           progress: null });
    if (config) checkLocal();
  }
}

/** Forget everything (sign-out). */
export function resetLocal() {
  config = null;
  checking = null;
  cache.clear();
  inflight.clear();
  emit({ status: "off", provider: null, reason: "", progress: null });
}

export function timeoutMs() {
  return Number(config?.timeout_ms) || DEFAULT_TIMEOUT_MS;
}

async function webProvider() {
  // Compiled out of the iOS/Android builds (native-prepare sets VITE_NATIVE),
  // so the browser engine's ~29 MB of WebAssembly never ships in an app that
  // has a native engine of its own.
  if (import.meta.env.VITE_NATIVE === "1") return null;
  try {
    const mod = await import("./providers/birefnetWeb.js");
    mod.BiRefNetWebProvider.configure(config?.web_model || null);
    return mod.BiRefNetWebProvider;
  } catch (e) {
    return null;
  }
}

/** The engine this device would use, or null. */
async function localProvider() {
  const allowed = config?.providers || [];
  const platform = nativePlatform();
  if (platform === "ios") return allowed.includes("ios") ? AppleVisionProvider : null;
  if (platform === "android") return allowed.includes("android") ? GoogleMLKitProvider : null;
  return allowed.includes("web") ? webProvider() : null;
}

/** Find out (once) whether this device can cut, and how ready it is.
    Status: "off" (not allowed here), "unsupported", "needs-download",
    "ready". Never throws. */
export function checkLocal({ force = false } = {}) {
  if (checking && !force) return checking;
  checking = (async () => {
    const provider = config ? await localProvider() : null;
    if (!provider) {
      emit({ status: "off", provider: null, reason: config ? "unsupported" : "off" });
      return snapshot;
    }
    emit({ status: "checking", provider: provider.id });
    const r = await provider.isSupported();
    if (!r.supported) {
      emit({ status: "unsupported", provider: provider.id, reason: r.reason || "unsupported" });
    } else {
      emit({ status: r.ready ? "ready" : "needs-download", provider: provider.id,
             reason: r.ready ? "" : (r.reason || "model_not_downloaded") });
    }
    return snapshot;
  })();
  return checking;
}

/** Make the device's engine ready -- download its model if it has one to
    download -- reporting progress on the shared snapshot. */
export async function prepareLocal() {
  const state = await checkLocal();
  if (state.status === "ready") return snapshot;
  if (state.status !== "needs-download") return snapshot;
  const provider = await localProvider();
  if (!provider) return snapshot;
  emit({ status: "downloading", progress: 0 });
  const r = await provider.initialize({ onProgress: (p) => emit({ progress: p }) });
  emit({ status: r.ready ? "ready" : (r.supported ? "needs-download" : "unsupported"),
         reason: r.ready ? "" : (r.reason || "model_download_failed"), progress: null });
  return snapshot;
}

/** Whether a device cutout can be attempted right now (ready, or ready once
    its model downloads). */
export function localUsable(state = snapshot) {
  return state.status === "ready" || state.status === "needs-download"
    || state.status === "downloading";
}

// --- the matte -------------------------------------------------------------------

const cache = new Map();     // `${provider}:${sha}` -> {mask, width, height}
const inflight = new Map();  // same key -> Promise

function remember(key, value) {
  cache.delete(key);
  cache.set(key, value);
  while (cache.size > CACHE_SIZE) cache.delete(cache.keys().next().value);
}

function withDeadline(promise, ms, signal) {
  return new Promise((resolve, reject) => {
    let done = false;
    const finish = (fn, v) => { if (!done) { done = true; clearTimeout(timer); fn(v); } };
    const timer = setTimeout(() => finish(reject, new LocalCutoutError("timeout")), ms);
    if (signal) {
      if (signal.aborted) finish(reject, new LocalCutoutError("cancelled"));
      signal.addEventListener?.("abort",
        () => finish(reject, new LocalCutoutError("cancelled")), { once: true });
    }
    promise.then((v) => finish(resolve, v), (e) => finish(reject, e));
  });
}

/** Share of the matte at least half opaque, read off a small copy; null when
    this environment cannot decode it (the server checks regardless). */
export async function maskCoverage(blob) {
  try {
    if (typeof createImageBitmap !== "function") return null;
    const bmp = await createImageBitmap(blob);
    const side = 64;
    const canvas = typeof OffscreenCanvas === "function"
      ? new OffscreenCanvas(side, side)
      : Object.assign(document.createElement("canvas"), { width: side, height: side });
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(bmp, 0, 0, side, side);
    bmp.close?.();
    const px = ctx.getImageData(0, 0, side, side).data;
    let kept = 0;
    for (let i = 0; i < px.length; i += 4) {
      // A greyscale matte drawn into RGBA: brightness, unless it carries
      // real transparency, in which case alpha is the matte.
      const v = px[i + 3] < 255 ? px[i + 3] : px[i];
      if (v >= 128) kept += 1;
    }
    return kept / (side * side);
  } catch (e) {
    return null;
  }
}

/** A matte of the photo in `blob` from this device's engine.
    Throws LocalCutoutError. */
export async function makeMask(blob, { sourceSha = "", signal } = {}) {
  const state = await checkLocal();
  if (!localUsable(state)) throw new LocalCutoutError(state.reason || "unsupported");
  if (state.status !== "ready") {
    const ready = await prepareLocal();
    if (ready.status !== "ready") throw new LocalCutoutError(ready.reason || "model_unavailable");
  }
  const provider = await localProvider();
  if (!provider) throw new LocalCutoutError("unsupported");
  const key = sourceSha ? `${provider.id}:${sourceSha}` : "";
  if (key && cache.has(key)) {
    const hit = cache.get(key);
    remember(key, hit);
    return { ...hit, provider: provider.id, platform: provider.platform, cached: true };
  }
  if (key && inflight.has(key)) return inflight.get(key);
  const run = (async () => {
    const out = await withDeadline(provider.removeBackground(blob, { signal }),
                                   timeoutMs(), signal);
    if (!out?.mask || !out.mask.size) throw new LocalCutoutError("invalid_mask");
    const coverage = await maskCoverage(out.mask);
    if (coverage !== null && coverage < MIN_COVERAGE) {
      throw new LocalCutoutError("nothing_found");
    }
    if (key) remember(key, out);
    return { ...out, provider: provider.id, platform: provider.platform, cached: false };
  })();
  if (key) inflight.set(key, run);
  try {
    return await run;
  } finally {
    if (key) inflight.delete(key);
  }
}

// --- whole operations ------------------------------------------------------------

function form(fields) {
  const fd = new FormData();
  for (const [k, v] of Object.entries(fields)) {
    if (v === undefined || v === null) continue;
    if (v instanceof Blob) fd.append(k, v, k === "mask" ? "mask.png" : "photo.jpg");
    else fd.append(k, String(v));
  }
  return fd;
}

/** Save a matte as the listing photo (/api/cutout/apply). */
export function applyMask({ sessionId, name, mask, sourceSha, provider, platform,
                            origin = "studio", art = false, signal }) {
  return api("/api/cutout/apply", {
    method: "POST", signal,
    body: form({ session_id: sessionId, name, source_sha: sourceSha || "",
                 provider, platform, origin, art: art ? "true" : "false", mask }),
  });
}

/** The studio's preview of a matte (/api/cutout/compose): writes nothing. */
export function composeMask({ sessionId, name, blob, mask, background = "",
                              art = false, provider, platform, signal }) {
  return api("/api/cutout/compose", {
    method: "POST", signal,
    body: form({ mask, file: blob || undefined,
                 session_id: blob ? undefined : sessionId,
                 name: blob ? undefined : name,
                 background, art: art ? "true" : "false", provider, platform }),
  });
}

/** Take the background off one stored photo and SAVE it -- the device queue's
    operation. On the device first; Photoroom once, if `allowCloud`, when the
    device could not (unsupported, timed out, failed, or found nothing).
    Resolves {provider, version, updated_at}; throws the last reason. */
export async function cutPhoto({ sessionId, name, origin = "queue", allowCloud = false,
                                 signal } = {}) {
  let lastSha = "";
  let localError = null;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    // The photo itself unreachable throws straight out: nothing to fall
    // back TO.
    const source = await fetchPhoto(sessionId, name, { which: "source", signal });
    lastSha = source.sha;
    try {
      const m = await makeMask(source.blob, { sourceSha: source.sha, signal });
      const saved = await applyMask({ sessionId, name, mask: m.mask, sourceSha: source.sha,
                                      provider: m.provider, platform: m.platform,
                                      origin, signal });
      return { ...saved, provider: m.provider };
    } catch (e) {
      // The photo changed between the read and the save: read it once more.
      if (e?.status === 409 && attempt === 0 && !/live/i.test(e.message || "")) continue;
      if (e?.status === 409) throw e;
      // A 422 is the server finding the matte unusable -- as good as the
      // device failing, for deciding on the cloud.
      if (e instanceof LocalCutoutError || e?.status === 422) {
        localError = e;
        break;
      }
      throw e;
    }
  }
  if (allowCloud && localError?.code !== "cancelled") {
    const r = await PhotoroomProvider.removeBackground(null, {
      sessionId, name, sourceSha: lastSha, origin, intent: "save", signal });
    return { ...r, provider: PhotoroomProvider.id };
  }
  throw localError || new LocalCutoutError("inference_failed");
}

/** The studio's Remove background: a PREVIEW, nothing written.

    `blob` is the editor's canvas when it has been edited (crop, strokes);
    null means "the stored photo", whose segmentation source is the photo as
    shot -- so a photo that was already cut out is cut again from the shot,
    never from the composite on white.

    Resolves {image, provider, engine, art, mask?, sourceSha?, fromSource};
    a device cutout carries its matte so the studio can re-colour it, hand
    it over transparent, and save it without cutting again. */
export async function previewCut({ sessionId, name, blob = null, background = "",
                                   allowCloud = false, signal } = {}) {
  let source = blob;
  let sourceSha = "";
  if (!source) {
    const fetched = await fetchPhoto(sessionId, name, { which: "source", signal });
    source = fetched.blob;
    sourceSha = fetched.sha;
  }
  let localError;
  try {
    const m = await makeMask(source, { sourceSha, signal });
    const out = await composeMask({ sessionId, name, blob: blob || null, mask: m.mask,
                                    background, provider: m.provider,
                                    platform: m.platform, signal });
    return { image: out.image, engine: out.engine, art: !!out.art,
             provider: m.provider, platform: m.platform, mask: m.mask,
             sourceSha, fromSource: !blob };
  } catch (e) {
    if (!(e instanceof LocalCutoutError) && e?.status !== 422) throw e;
    localError = e;
  }
  if (allowCloud && localError?.code !== "cancelled") {
    const r = await PhotoroomProvider.removeBackground(blob, {
      sessionId, name, intent: "preview", signal });
    return { image: r.image, engine: r.engine, provider: PhotoroomProvider.id,
             fromSource: !blob, cloud: true };
  }
  throw localError;
}

export { LocalCutoutError };

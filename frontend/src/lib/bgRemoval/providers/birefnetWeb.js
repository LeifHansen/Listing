/* BiRefNetWebProvider: BiRefNet Lite in this browser -- the web app's
   on-device engine. Free, and the photo never leaves the page.

   Lazily loaded (service.js imports this module only when the server has
   switched the browser engine on), and lazily started: nothing is
   downloaded until a background is actually asked for. The model -- an
   fp16 WebGPU-friendly ONNX export, ~115 MB -- comes from the URL the
   server names, is checked against the SHA-256 it names, and is cached on
   the device, so it is fetched once.

   Everything heavy happens in a worker (web/birefnet.worker.js). The worker
   is kept while photos keep coming and let go after IDLE_MS without one,
   which frees the GPU memory the session holds.

   SWITCHED OFF BY DEFAULT on the server (BG_LOCAL_PROVIDERS without "web")
   until the model's licence is signed off: the weights are MIT, but one of
   the datasets they were trained on (DIS5K) forbids commercial use. */
import { LocalCutoutError } from "../errors";
import { rememberTooSlow, webCapability } from "../web/capabilities";

const IDLE_MS = 3 * 60 * 1000;
// A WebAssembly run that takes longer than this has shown this machine is
// not one to ask again.
const TOO_SLOW_MS = 30 * 1000;

let model = null;       // {url, sha256, bytes}
let capability = null;  // memoised webCapability()
let worker = null;
let ready = null;       // Promise resolving when the worker has the session
let idleTimer = null;
let nextId = 1;
const waiting = new Map();
let progressListener = null;

function stopWorker() {
  if (idleTimer) clearTimeout(idleTimer);
  idleTimer = null;
  try { worker?.terminate(); } catch (e) { /* already gone */ }
  worker = null;
  ready = null;
  for (const [, w] of waiting) w.reject(new LocalCutoutError("cancelled"));
  waiting.clear();
}

function touch() {
  if (idleTimer) clearTimeout(idleTimer);
  idleTimer = setTimeout(stopWorker, IDLE_MS);
}

function startWorker() {
  if (worker) return ready;
  worker = new Worker(new URL("../web/birefnet.worker.js", import.meta.url), { type: "module" });
  ready = new Promise((resolve, reject) => {
    worker.onmessage = (event) => {
      const msg = event.data || {};
      if (msg.type === "progress") {
        const total = msg.total || model?.bytes || 0;
        if (total && progressListener) progressListener(Math.min(1, msg.loaded / total));
        return;
      }
      if (msg.type === "loaded") { resolve(); return; }
      if (msg.type === "mask" || (msg.type === "error" && msg.id)) {
        const w = waiting.get(msg.id);
        waiting.delete(msg.id);
        if (!w) return;
        if (msg.type === "mask") w.resolve(msg);
        else w.reject(new LocalCutoutError(msg.code));
        return;
      }
      if (msg.type === "error") reject(new LocalCutoutError(msg.code || "model_download_failed"));
    };
    worker.onerror = () => reject(new LocalCutoutError("inference_failed"));
  });
  worker.postMessage({ type: "load", url: model.url, sha256: model.sha256,
                       backend: capability?.backend || "wasm" });
  ready.catch(() => stopWorker());
  return ready;
}

export const BiRefNetWebProvider = {
  id: "birefnet-web",
  platform: "web",

  /** The model the server names (status.local.web_model), or null. */
  configure(next) {
    const changed = JSON.stringify(next || null) !== JSON.stringify(model);
    model = next && next.url && next.sha256 ? next : null;
    if (changed) stopWorker();
  },

  async isSupported() {
    if (!model) return { supported: false, ready: false, reason: "off" };
    capability = capability || await webCapability();
    if (!capability.supported) {
      return { supported: false, ready: false, reason: capability.reason };
    }
    // Ready as soon as the worker holds a session; until then the model
    // still has to come down (or out of the cache) once.
    return { supported: true, ready: !!worker, reason: worker ? "" : "model_not_downloaded" };
  },

  async initialize({ onProgress } = {}) {
    const s = await this.isSupported();
    if (!s.supported) return s;
    progressListener = onProgress || null;
    try {
      await startWorker();
      touch();
      return { supported: true, ready: true, reason: "" };
    } catch (e) {
      return { supported: true, ready: false, reason: e?.code || "model_download_failed" };
    } finally {
      progressListener = null;
    }
  },

  async removeBackground(blob) {
    if (!worker) {
      const r = await this.initialize();
      if (!r.ready) throw new LocalCutoutError(r.reason || "model_unavailable");
    }
    await ready;
    touch();
    const id = nextId++;
    const started = Date.now();
    const out = await new Promise((resolve, reject) => {
      waiting.set(id, { resolve, reject });
      worker.postMessage({ type: "segment", id, blob });
    });
    if (capability?.backend === "wasm" && Date.now() - started > TOO_SLOW_MS) {
      rememberTooSlow();
    }
    touch();
    return { mask: out.mask, width: out.width, height: out.height };
  },
};

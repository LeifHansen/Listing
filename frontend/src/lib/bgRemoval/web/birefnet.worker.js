/* BiRefNet Lite in a Web Worker: the browser's own background remover.

   Off the main thread end to end -- the download, the SHA-256 check, the
   session, the pre- and post-processing -- so the page never stutters while
   a photo is cut. One session per worker, built once and reused for every
   photo; the provider terminates the worker after a while idle, which is
   what gives the GPU memory back.

   THE MODEL is fetched from the URL the server names (BG_WEB_MODEL_URL) and
   must match the SHA-256 it names, before it is cached or used: a corrupted
   or substituted file is never run. It is kept in Cache Storage under that
   hash, so a second visit reads it from disk and a new model (a new hash)
   never collides with the old one.

   THE BACKEND is WebGPU where the provider found a capable adapter, else
   WebAssembly -- single-threaded, since the app is not cross-origin
   isolated (onnxruntime-web forces one thread without SharedArrayBuffer).

   Messages in:  {type: "load", url, sha256, backend}
                 {type: "segment", id, blob}
   Messages out: {type: "progress", loaded, total}
                 {type: "loaded"} | {type: "error", id?, code, message}
                 {type: "mask", id, mask: Blob, width, height, ms} */
import * as ort from "onnxruntime-web/webgpu";
import wasmUrl from "onnxruntime-web/ort-wasm-simd-threaded.asyncify.wasm?url";
import {
  INPUT_SIDE, matteToRGBA, sha256Hex, toInputTensor, toMatte,
} from "./tensors";

const CACHE = "thryft-bg-model";

ort.env.wasm.wasmPaths = { wasm: wasmUrl };
ort.env.wasm.numThreads = self.crossOriginIsolated ? Math.min(4, navigator.hardwareConcurrency || 1) : 1;
ort.env.logLevel = "error";

let session = null;
let loading = null;

function fail(code, message, id) {
  self.postMessage({ type: "error", id, code, message });
}

async function readModel(url, sha256) {
  const key = `${url}#${sha256}`;
  let cache;
  try {
    cache = await caches.open(CACHE);
    const hit = await cache.match(key);
    if (hit) {
      const buf = await hit.arrayBuffer();
      if (await sha256Hex(buf) === sha256) return buf;
      await cache.delete(key);
    }
  } catch (e) {
    cache = null;   // no Cache Storage here (private mode): download each time
  }
  const res = await fetch(url, { mode: "cors", credentials: "omit" });
  if (!res.ok || !res.body) throw Object.assign(new Error(`HTTP ${res.status}`), { code: "model_download_failed" });
  const total = Number(res.headers.get("content-length")) || 0;
  const reader = res.body.getReader();
  const parts = [];
  let loaded = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value);
    loaded += value.byteLength;
    self.postMessage({ type: "progress", loaded, total });
  }
  const bytes = new Uint8Array(loaded);
  let at = 0;
  for (const p of parts) { bytes.set(p, at); at += p.byteLength; }
  if (await sha256Hex(bytes.buffer) !== sha256) {
    throw Object.assign(new Error("The model did not match its checksum."), { code: "model_download_failed" });
  }
  if (cache) {
    try {
      await cache.put(key, new Response(bytes, { headers: { "content-type": "application/octet-stream" } }));
    } catch (e) { /* over quota: it still runs, it just downloads next time */ }
  }
  return bytes.buffer;
}

async function load({ url, sha256, backend }) {
  if (session) return session;
  if (loading) return loading;
  loading = (async () => {
    const model = await readModel(url, sha256);
    const providers = backend === "webgpu" ? ["webgpu", "wasm"] : ["wasm"];
    session = await ort.InferenceSession.create(model, {
      executionProviders: providers,
      graphOptimizationLevel: "all",
    });
    return session;
  })();
  try {
    return await loading;
  } finally {
    loading = null;
  }
}

async function segment(blob) {
  const started = performance.now();
  const bitmap = await createImageBitmap(blob);
  const { width, height } = bitmap;
  // The whole photo squashed to the model's square, as the reference
  // implementation does; the matte is stretched back to the photo below.
  const square = new OffscreenCanvas(INPUT_SIDE, INPUT_SIDE);
  const sctx = square.getContext("2d", { willReadFrequently: true });
  sctx.drawImage(bitmap, 0, 0, INPUT_SIDE, INPUT_SIDE);
  bitmap.close?.();
  const rgba = sctx.getImageData(0, 0, INPUT_SIDE, INPUT_SIDE).data;
  const input = new ort.Tensor("float32", toInputTensor(rgba), [1, 3, INPUT_SIDE, INPUT_SIDE]);
  const outputs = await session.run({ [session.inputNames[0]]: input });
  const out = outputs[session.outputNames[session.outputNames.length - 1]];
  const matte = toMatte(out.data);
  input.dispose?.();
  out.dispose?.();
  // Back to the photo's own size, smoothed -- the server refines it again
  // against the photo, but a matte that already fits is one fewer resample.
  const small = new OffscreenCanvas(INPUT_SIDE, INPUT_SIDE);
  small.getContext("2d").putImageData(new ImageData(matteToRGBA(matte), INPUT_SIDE, INPUT_SIDE), 0, 0);
  const full = new OffscreenCanvas(width, height);
  const fctx = full.getContext("2d");
  fctx.imageSmoothingEnabled = true;
  fctx.imageSmoothingQuality = "high";
  fctx.drawImage(small, 0, 0, width, height);
  const mask = await full.convertToBlob({ type: "image/png" });
  return { mask, width, height, ms: Math.round(performance.now() - started) };
}

self.onmessage = async (event) => {
  const msg = event.data || {};
  if (msg.type === "load") {
    try {
      await load(msg);
      self.postMessage({ type: "loaded" });
    } catch (e) {
      fail(e?.code || "model_download_failed", String(e?.message || e));
    }
    return;
  }
  if (msg.type === "segment") {
    if (!session) {
      fail("model_unavailable", "The model is not loaded.", msg.id);
      return;
    }
    try {
      const out = await segment(msg.blob);
      self.postMessage({ type: "mask", id: msg.id, ...out });
    } catch (e) {
      const text = String(e?.message || e);
      fail(/memory|alloc/i.test(text) ? "out_of_memory" : "inference_failed", text, msg.id);
    }
  }
};

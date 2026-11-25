/* The arithmetic between a photo and BiRefNet, and between BiRefNet and a
   matte. Pure functions over typed arrays -- no canvas, no model -- so they
   are tested where the model cannot run (jsdom), and the worker only moves
   pixels in and out of them.

   BiRefNet Lite takes a 1024x1024 RGB image, normalised with ImageNet's mean
   and standard deviation, in NCHW order, and answers one channel the same
   size: logits in the reference implementation (sigmoid applied after), or
   probabilities in exports that bake the sigmoid in. Which one is read off
   the values themselves, as transformers.js does: anything outside 0..1 is a
   logit. */

export const INPUT_SIDE = 1024;
const MEAN = [0.485, 0.456, 0.406];
const STD = [0.229, 0.224, 0.225];

/** RGBA bytes (side x side) -> Float32 NCHW, ImageNet-normalised. */
export function toInputTensor(rgba, side = INPUT_SIDE) {
  const plane = side * side;
  const out = new Float32Array(3 * plane);
  for (let i = 0, p = 0; p < plane; i += 4, p += 1) {
    out[p] = (rgba[i] / 255 - MEAN[0]) / STD[0];
    out[plane + p] = (rgba[i + 1] / 255 - MEAN[1]) / STD[1];
    out[2 * plane + p] = (rgba[i + 2] / 255 - MEAN[2]) / STD[2];
  }
  return out;
}

/** Whether a model output is logits (needs a sigmoid) rather than 0..1. */
export function isLogits(values) {
  // A stride keeps this cheap on a million values; logits that never leave
  // 0..1 anywhere on a photo would be a model that saw nothing at all.
  const step = Math.max(1, Math.floor(values.length / 4096));
  for (let i = 0; i < values.length; i += step) {
    const v = values[i];
    if (v < 0 || v > 1) return true;
  }
  return false;
}

/** Model output -> 8-bit matte, soft: no threshold, ever. */
export function toMatte(values) {
  const logits = isLogits(values);
  const out = new Uint8ClampedArray(values.length);
  for (let i = 0; i < values.length; i++) {
    const p = logits ? 1 / (1 + Math.exp(-values[i])) : values[i];
    out[i] = Math.round(p * 255);
  }
  return out;
}

/** 8-bit matte -> RGBA bytes with the matte as grey (for a canvas to scale
    and encode: a canvas cannot encode a single channel). */
export function matteToRGBA(matte) {
  const out = new Uint8ClampedArray(matte.length * 4);
  for (let p = 0, i = 0; p < matte.length; p += 1, i += 4) {
    out[i] = out[i + 1] = out[i + 2] = matte[p];
    out[i + 3] = 255;
  }
  return out;
}

/** Share of the matte at least half opaque. */
export function coverage(matte) {
  let kept = 0;
  for (let i = 0; i < matte.length; i++) if (matte[i] >= 128) kept += 1;
  return matte.length ? kept / matte.length : 0;
}

/** SHA-256 of an ArrayBuffer, as lowercase hex. */
export async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/* The arithmetic around BiRefNet in the browser, and the decision of whether
 * this browser should run it at all.
 *
 * The model itself cannot run in jsdom; everything either side of it can,
 * and is where the quiet mistakes live: the wrong normalisation produces a
 * plausible-looking matte of the wrong thing, a sigmoid applied twice (or
 * never) flattens every edge to grey or to a hard step, and a threshold
 * anywhere throws away the soft band that hair, fur and glass live in.
 */
import { webcrypto } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  coverage, isLogits, matteToRGBA, sha256Hex, toInputTensor, toMatte,
} from "./tensors";
import { MIN_BINDING, MIN_BUFFER, assessAdapter, assessCpu, webCapability } from "./capabilities";

describe("feeding the model", () => {
  it("normalises with ImageNet's mean and deviation, channel-planar", () => {
    // A 2x2 image: red, mid grey, black, black.
    const rgba = new Uint8ClampedArray([255, 0, 0, 255, 128, 128, 128, 255,
                                        0, 0, 0, 255, 0, 0, 0, 255]);
    const t = toInputTensor(rgba, 2);
    expect(t).toBeInstanceOf(Float32Array);
    expect(t.length).toBe(12);                                   // 3 planes of 4
    expect(t[0]).toBeCloseTo((1 - 0.485) / 0.229, 5);            // R plane, red pixel
    expect(t[1]).toBeCloseTo((128 / 255 - 0.485) / 0.229, 5);    // R plane, grey pixel
    expect(t[4]).toBeCloseTo((0 - 0.456) / 0.224, 5);            // G plane, red pixel
    expect(t[8]).toBeCloseTo((0 - 0.406) / 0.225, 5);            // B plane, red pixel
  });
});

describe("reading the model", () => {
  it("knows logits from probabilities by their values", () => {
    expect(isLogits(new Float32Array([-4, 0.5, 6]))).toBe(true);
    expect(isLogits(new Float32Array([0, 0.25, 1]))).toBe(false);
  });

  it("applies the sigmoid to logits and only to logits", () => {
    expect([...toMatte(new Float32Array([-20, 0, 20]))]).toEqual([0, 128, 255]);
    expect([...toMatte(new Float32Array([0, 0.5, 1]))]).toEqual([0, 128, 255]);
  });

  it("keeps the soft band soft: no threshold anywhere", () => {
    const m = toMatte(new Float32Array([0.1, 0.3, 0.45, 0.55, 0.7, 0.9]));
    // (Float32 in, as the model answers: 0.7 is 0.69999998..., hence 178.)
    expect([...m]).toEqual([26, 77, 115, 140, 178, 229]);
  });

  it("draws a matte as grey a canvas can scale and encode", () => {
    expect([...matteToRGBA(new Uint8ClampedArray([0, 200]))])
      .toEqual([0, 0, 0, 255, 200, 200, 200, 255]);
  });

  it("measures what the matte kept", () => {
    expect(coverage(new Uint8ClampedArray([0, 127, 128, 255]))).toBe(0.5);
    expect(coverage(new Uint8ClampedArray([]))).toBe(0);
  });

  it("names a model's bytes by SHA-256, as the server does", async () => {
    vi.stubGlobal("crypto", webcrypto);
    const hex = await sha256Hex(new TextEncoder().encode("abc").buffer);
    expect(hex).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });
});

afterEach(() => vi.unstubAllGlobals());

describe("whether this browser should run it", () => {
  const big = { maxBufferSize: MIN_BUFFER * 2, maxStorageBufferBindingSize: MIN_BINDING * 2 };

  it("takes a real GPU with room for the model", () => {
    expect(assessAdapter({ limits: big, features: new Set(["shader-f16"]) }))
      .toEqual({ ok: true, reason: "", fp16: true });
  });

  it("refuses the software fallback adapter, whatever it claims", () => {
    expect(assessAdapter({ limits: big, info: { isFallbackAdapter: true } }).ok).toBe(false);
    expect(assessAdapter({ limits: big, isFallbackAdapter: true }).ok).toBe(false);
    expect(assessAdapter(null).ok).toBe(false);
  });

  it("refuses a GPU without room for the model's buffers", () => {
    expect(assessAdapter({ limits: { maxBufferSize: 64 << 20,
                                     maxStorageBufferBindingSize: 64 << 20 } }))
      .toMatchObject({ ok: false, reason: "low_memory" });
  });

  it("runs on the CPU only on a desktop-class machine", () => {
    expect(assessCpu({ deviceMemory: 16, hardwareConcurrency: 12, mobile: false }).ok).toBe(true);
    expect(assessCpu({ deviceMemory: 16, hardwareConcurrency: 12, mobile: true }).ok).toBe(false);
    expect(assessCpu({ deviceMemory: 4, hardwareConcurrency: 12, mobile: false }).ok).toBe(false);
    expect(assessCpu({ deviceMemory: undefined, hardwareConcurrency: 4, mobile: false }).ok).toBe(false);
  });
});

describe("the whole decision, on a stand-in browser", () => {
  it("says no where the browser lacks the pieces at all", async () => {
    vi.stubGlobal("OffscreenCanvas", undefined);
    expect(await webCapability()).toMatchObject({ supported: false });
  });

  it("picks WebGPU from the adapter the browser actually offers", async () => {
    vi.stubGlobal("OffscreenCanvas", class {});
    vi.stubGlobal("Worker", class {});
    vi.stubGlobal("navigator", { ...navigator, deviceMemory: 8, hardwareConcurrency: 8,
      gpu: { requestAdapter: async () => ({ limits: big2(), features: new Set() }) } });
    expect(await webCapability()).toMatchObject({ supported: true, backend: "webgpu", fp16: false });
  });
});

function big2() {
  return { maxBufferSize: MIN_BUFFER * 2, maxStorageBufferBindingSize: MIN_BINDING * 2 };
}

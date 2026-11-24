/* Can THIS browser run BiRefNet Lite, and how? Read off the hardware the
   browser actually reports -- WebGPU's adapter and its limits, the memory
   and cores it admits to -- never off the browser's or the phone's name.

   WebGPU first: a real (not software-fallback) adapter whose buffers can hold
   the model's largest tensor, with shader-f16 for the half-precision model.
   WebAssembly only on desktop-class machines: without cross-origin isolation
   (which this app does not turn on -- it would break eBay's images and the
   photo bucket) it runs single-threaded, minutes per photo on a phone, so a
   phone without WebGPU is better served by the cloud. And a device that has
   already proved too slow is remembered, so it is not asked again. */
import { readLocal, writeLocal } from "@/lib/localPrefs";

// BiRefNet Lite's largest single tensor at 1024x1024 is ~400 MB in fp32 and
// about half that in fp16; these are the floors under which a session either
// fails to build or thrashes.
export const MIN_BUFFER = 256 * 1024 * 1024;
export const MIN_BINDING = 128 * 1024 * 1024;
const TOO_SLOW_KEY = "bg-web-too-slow";

/** {ok, reason, fp16} for a WebGPU adapter (or null). Pure. */
export function assessAdapter(adapter) {
  if (!adapter) return { ok: false, reason: "no_webgpu" };
  const fallback = adapter.info?.isFallbackAdapter ?? adapter.isFallbackAdapter;
  if (fallback) return { ok: false, reason: "no_webgpu" };
  const limits = adapter.limits || {};
  if ((limits.maxBufferSize || 0) < MIN_BUFFER
      || (limits.maxStorageBufferBindingSize || 0) < MIN_BINDING) {
    return { ok: false, reason: "low_memory" };
  }
  const fp16 = !!adapter.features?.has?.("shader-f16");
  return { ok: true, reason: "", fp16 };
}

/** Whether this machine may run the model on WebAssembly instead. Pure. */
export function assessCpu({ deviceMemory, hardwareConcurrency, mobile }) {
  if (mobile) return { ok: false, reason: "no_webgpu" };
  if (deviceMemory !== undefined && deviceMemory < 8) return { ok: false, reason: "low_memory" };
  if ((hardwareConcurrency || 0) < 8) return { ok: false, reason: "too_slow" };
  return { ok: true, reason: "" };
}

export function rememberedTooSlow() {
  return readLocal(TOO_SLOW_KEY) === "1";
}

export function rememberTooSlow() {
  writeLocal(TOO_SLOW_KEY, "1");
}

function isMobile() {
  try {
    if (navigator.userAgentData && "mobile" in navigator.userAgentData) {
      return !!navigator.userAgentData.mobile;
    }
    return window.matchMedia?.("(pointer: coarse)")?.matches && !window.matchMedia?.("(pointer: fine)")?.matches;
  } catch (e) {
    return false;
  }
}

/** {supported, backend: "webgpu"|"wasm", fp16, reason}. Never throws. */
export async function webCapability() {
  try {
    if (typeof Worker !== "function" || typeof WebAssembly !== "object"
        || typeof OffscreenCanvas !== "function") {
      return { supported: false, reason: "no_webgpu" };
    }
    if (rememberedTooSlow()) return { supported: false, reason: "too_slow" };
    if (navigator.deviceMemory !== undefined && navigator.deviceMemory < 4) {
      return { supported: false, reason: "low_memory" };
    }
    let adapter = null;
    try {
      adapter = navigator.gpu
        ? await navigator.gpu.requestAdapter({ powerPreference: "high-performance" })
        : null;
    } catch (e) {
      adapter = null;
    }
    const gpu = assessAdapter(adapter);
    if (gpu.ok) return { supported: true, backend: "webgpu", fp16: gpu.fp16, reason: "" };
    const cpu = assessCpu({ deviceMemory: navigator.deviceMemory,
                            hardwareConcurrency: navigator.hardwareConcurrency,
                            mobile: isMobile() });
    if (cpu.ok) return { supported: true, backend: "wasm", fp16: false, reason: "" };
    return { supported: false, reason: gpu.reason === "low_memory" ? gpu.reason : cpu.reason };
  } catch (e) {
    return { supported: false, reason: "no_webgpu" };
  }
}

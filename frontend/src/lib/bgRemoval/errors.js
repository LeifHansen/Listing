/* Why a cutout could not be made on this device -- each with a sentence the
   seller can read, and a code the service decides on. */

export const REASONS = {
  off: "Background removal on this device is switched off right now.",
  unsupported: "This device can't remove backgrounds itself.",
  os_version: "Removing backgrounds on this device needs a newer system version.",
  simulator: "Background removal doesn't run in the simulator.",
  play_services: "Removing backgrounds on this phone needs Google Play services.",
  model_unavailable: "The background remover is still downloading on this device.",
  model_not_downloaded: "The background remover hasn't downloaded on this device yet.",
  model_download_failed: "The background remover couldn't be downloaded on this device.",
  no_webgpu: "This browser can't run the background remover.",
  low_memory: "This device doesn't have the memory to remove backgrounds itself.",
  too_slow: "This device is too slow to remove backgrounds itself.",
  timeout: "Removing the background took too long on this device.",
  nothing_found: "No item was found in this photo — it was kept as shot.",
  invalid_mask: "The cutout came back empty — the photo was kept as shot.",
  inference_failed: "The cutout failed on this device.",
  out_of_memory: "This device ran out of memory removing that background.",
  bad_input: "That photo couldn't be read.",
  too_large: "That photo is too large to cut on this device.",
  cancelled: "Stopped.",
};

/** A cutout this device could not make. `code` is one of REASONS' keys (or
    the native plugin's own code); `message` is for the seller. */
export class LocalCutoutError extends Error {
  constructor(code, message) {
    super(message || REASONS[code] || REASONS.inference_failed);
    this.name = "LocalCutoutError";
    this.code = code || "inference_failed";
  }
}

/** Codes that are about the DEVICE, not the photo: no point trying the next
    photo on it either. */
export const DEVICE_CODES = new Set([
  "off", "unsupported", "os_version", "simulator", "play_services",
  "model_download_failed", "no_webgpu", "low_memory", "too_slow",
]);

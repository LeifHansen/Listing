/* The two on-device engines that live in the app's own native plugin
   (frontend/native/thryft-cutout): Apple Vision on iOS 17+, Google ML Kit
   subject segmentation on Android.

   Reached through Capacitor's global registry, like every other plugin this
   app uses (see lib/platform.js), so the web bundle carries no native import
   and an older app build without the plugin simply reports it unsupported.

   Each takes the photo's bytes and answers a soft greyscale matte of it --
   never a finished picture; the server composites (services/cutouts). */
import { nativePlatform } from "@/lib/platform";
import { LocalCutoutError } from "../errors";

function plugin() {
  try {
    return window.Capacitor?.Plugins?.ThryftCutout || null;
  } catch (e) {
    return null;
  }
}

async function blobToBase64(blob) {
  const buf = new Uint8Array(await blob.arrayBuffer());
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < buf.length; i += CHUNK) {
    bin += String.fromCharCode.apply(null, buf.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}

function base64ToBlob(b64, type = "image/png") {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return new Blob([out], { type });
}

function nativeProvider({ id, platform }) {
  return {
    id,
    platform,
    /** {supported, ready, reason} -- never throws. */
    async isSupported() {
      if (nativePlatform() !== platform) {
        return { supported: false, ready: false, reason: "unsupported" };
      }
      const p = plugin();
      if (!p?.isSupported) {
        return { supported: false, ready: false, reason: "unsupported" };
      }
      try {
        const r = await p.isSupported();
        return { supported: !!r?.supported, ready: !!r?.ready,
                 reason: r?.reason || (r?.supported ? "" : "unsupported") };
      } catch (e) {
        return { supported: false, ready: false, reason: "unsupported" };
      }
    },
    /** Make the engine ready (Android: download the model through Play
        services). `onProgress(0..1)` while it downloads. */
    async initialize({ onProgress } = {}) {
      const p = plugin();
      if (!p?.prepare) return { supported: false, ready: false, reason: "unsupported" };
      let handle = null;
      try {
        if (onProgress && p.addListener) {
          handle = await p.addListener("downloadProgress",
            (e) => onProgress(Number(e?.progress) || 0));
        }
        const r = await p.prepare();
        return { supported: !!r?.supported, ready: !!r?.ready, reason: r?.reason || "" };
      } catch (e) {
        return { supported: true, ready: false, reason: "model_download_failed" };
      } finally {
        try { await handle?.remove?.(); } catch (e) { /* already gone */ }
      }
    },
    /** {mask: Blob, width, height} for the photo in `blob`. */
    async removeBackground(blob) {
      const p = plugin();
      if (!p?.segment) throw new LocalCutoutError("unsupported");
      let r;
      try {
        r = await p.segment({ image: await blobToBase64(blob) });
      } catch (e) {
        throw new LocalCutoutError(e?.code || "inference_failed",
                                   e?.code ? undefined : e?.message);
      }
      if (!r?.mask) throw new LocalCutoutError("invalid_mask");
      return { mask: base64ToBlob(r.mask), width: Number(r.width) || 0,
               height: Number(r.height) || 0 };
    },
  };
}

export const AppleVisionProvider = nativeProvider({ id: "apple-vision", platform: "ios" });
export const GoogleMLKitProvider = nativeProvider({ id: "mlkit", platform: "android" });

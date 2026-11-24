/* Photoroom, the cloud fallback -- through this app's own server, which holds
   the key, checks the seller's consent switch, charges the token and counts
   the month, exactly as for every Photoroom cutout. The browser never sees a
   Photoroom credential.

   Two intents: "preview" is the studio's existing Remove background
   (/api/image/remove-bg, nothing written); "save" is the device queue's
   fallback for one stored photo (/api/cutout/cloud), which is never paid
   for twice for an unchanged photo. */
import { api, MODEL_TIMEOUT_MS } from "@/lib/api";

export const PhotoroomProvider = {
  id: "photoroom",
  platform: "server",
  async isSupported() {
    return { supported: true, ready: true, reason: "" };
  },
  async initialize() {
    return { supported: true, ready: true, reason: "" };
  },
  async removeBackground(blob, { sessionId, name, sourceSha = "", origin = "queue",
                                 intent = "preview", signal } = {}) {
    const fd = new FormData();
    fd.append("session_id", sessionId || "");
    fd.append("name", name || "");
    if (intent === "save") {
      fd.append("source_sha", sourceSha || "");
      fd.append("origin", origin);
      const r = await api("/api/cutout/cloud", { method: "POST", body: fd,
                                                 timeoutMs: MODEL_TIMEOUT_MS, signal });
      return { saved: true, version: r.version, updated_at: r.updated_at, engine: r.engine };
    }
    if (blob) fd.append("file", new File([blob], name || "photo.jpg", { type: blob.type }));
    const r = await api("/api/image/remove-bg", { method: "POST", body: fd,
                                                  timeoutMs: MODEL_TIMEOUT_MS, signal });
    return { image: r.image, engine: r.engine };
  },
};

/* Placeholder until the browser engine lands: reports itself unsupported, so
   the web app behaves exactly as before (Photoroom only). */
export const BiRefNetWebProvider = {
  id: "birefnet-web",
  platform: "web",
  configure() {},
  async isSupported() { return { supported: false, ready: false, reason: "unsupported" }; },
  async initialize() { return { supported: false, ready: false, reason: "unsupported" }; },
  async removeBackground() { throw new Error("unsupported"); },
};

import type { CapacitorConfig } from "@capacitor/cli";

/**
 * Capacitor config for the Thryft Shop native app (iOS / Android).
 *
 * v1 strategy — the native shell loads the LIVE production site directly, so
 * the app IS the web app: same-origin, which means auth cookies, API calls and
 * CORS all work with ZERO code changes, and you can ship web updates without
 * rebuilding the native app. Great for TestFlight testing.
 *
 * For an App Store *release* you'll likely want to bundle `dist` locally and
 * point the API base at the production URL (+ enable CORS) so the app works
 * offline-ish and satisfies Apple's "minimum functionality" guideline. That
 * path is documented in MOBILE.md — it isn't needed to start testing.
 *
 * Change `appId` to match the App ID you registered in your Apple Developer
 * account, and `server.url` if you move to a custom domain.
 */
const config: CapacitorConfig = {
  appId: "com.thryftshop.app",
  appName: "Thryft Shop",
  webDir: "dist",
  server: {
    url: "https://listing-lfwjrg.fly.dev",
    cleartext: false,
  },
  ios: {
    // Let the web app own the safe-area insets (it already uses
    // viewport-fit=cover + env(safe-area-inset-*)).
    contentInset: "never",
  },
};

export default config;

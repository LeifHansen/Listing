/* Native-shell awareness.
 *
 * The iOS/Android app bundles THIS web build inside a Capacitor shell (no
 * remote server.url — Apple's guideline 4.2 treats a webview that just loads
 * a website as a "repackaged website"). Bundled, the page's origin is
 * capacitor://localhost, so the API lives on a different origin and every
 * request needs an absolute base.
 *
 * API_BASE is baked in at build time: scripts/ios-prepare.sh builds with
 * VITE_API_BASE=https://<the server>, while the web deploy builds without it,
 * so the web bundle keeps today's relative URLs exactly.
 */

export const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/+$/, "");

// True inside the Capacitor shell. The native runtime injects window.Capacitor
// into the bundled page; the plain web build never has it.
export function isNative() {
  try {
    return !!window.Capacitor?.isNativePlatform?.();
  } catch (e) {
    return false;
  }
}

// Absolute URL for an app path ("/api/...", "/media/..."). On the web build
// API_BASE is "" and this is the identity function.
export function apiUrl(path) {
  return path && path.startsWith("/") ? API_BASE + path : path;
}

// --- session token (native only) -------------------------------------------
// The web app authenticates with an httponly cookie, which a cross-origin
// fetch from the shell never carries. The auth endpoints already return a
// bearer token for exactly this case; it's stored only in the native build
// (API_BASE set) so the web app keeps its cookie-only posture.

const TOKEN_KEY = "thryft-session-token";

export function storedToken() {
  if (!API_BASE) return null;
  try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
}

export function storeToken(token) {
  if (!API_BASE) return;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch (e) { /* private mode — session lasts until the app closes */ }
}

// --- external browser -------------------------------------------------------
// Purchases MUST leave the app on iOS: completing a non-Apple checkout inside
// the webview is a guideline 3.1.1 rejection, while a link out to the system
// browser is allowed (US storefront). Try the Capacitor Browser plugin, then
// window.open. Returns false when neither could open — the caller says so
// instead of silently falling back to in-webview navigation, which would
// trade an error message for an App Store rejection.
export async function openExternal(url) {
  try {
    const browser = window.Capacitor?.Plugins?.Browser;
    if (browser?.open) {
      await browser.open({ url });
      return true;
    }
  } catch (e) { /* fall through */ }
  try {
    const w = window.open(url, "_blank");
    if (w) return true;
  } catch (e) { /* fall through */ }
  return false;
}

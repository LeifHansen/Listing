/* Native-shell awareness.
 *
 * The iOS/Android app bundles THIS web build inside a Capacitor shell (no
 * remote server.url — Apple's guideline 4.2 treats a webview that just loads
 * a website as a "repackaged website"). Bundled, the page's origin is
 * capacitor://localhost, so the API lives on a different origin and every
 * request needs an absolute base.
 *
 * API_BASE is baked in at build time: `npm run native:*` builds with
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
//
// That token used to live in localStorage, which on a native shell is an
// ordinary file inside the app container: readable by anything with access to
// a device backup, and not protected by the device passcode. A long-lived
// credential to someone's eBay-connected account does not belong there. It
// now lives in the Keychain (iOS) / EncryptedSharedPreferences over the
// Keystore (Android) via capacitor-secure-storage-plugin.
//
// The plugin is reached through Capacitor's global registry rather than an
// import, so the WEB bundle neither grows nor changes behaviour, and a native
// build whose plugin hasn't been synced yet still works — it falls back to
// localStorage rather than logging everyone out.

const TOKEN_KEY = "thryft-session-token";

function securePlugin() {
  try {
    return isNative() ? window.Capacitor?.Plugins?.SecureStoragePlugin || null : null;
  } catch (e) {
    return null;
  }
}

// The synchronous answer for the request wrapper. Hydrated by loadToken()
// before the first authenticated call (see tokenReady).
let memoryToken = null;
let readyPromise = null;

function localToken() {
  try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
}

async function loadToken() {
  const secure = securePlugin();
  if (!secure) {
    memoryToken = localToken();
    return;
  }
  try {
    const { value } = await secure.get({ key: TOKEN_KEY });
    if (value) {
      memoryToken = value;
      // Anything left in localStorage from a build that predates this is a
      // copy of the same credential sitting in the clear. Now that the
      // Keychain has it, that copy is pure liability.
      try { localStorage.removeItem(TOKEN_KEY); } catch (e) { /* ignore */ }
      return;
    }
  } catch (e) { /* nothing stored yet — the plugin rejects on a missing key */ }
  // Migrate a token from the old location, then delete it from there. Doing
  // this instead of ignoring it is what keeps the upgrade from signing
  // everyone out.
  const legacy = localToken();
  if (legacy) {
    memoryToken = legacy;
    try {
      await secure.set({ key: TOKEN_KEY, value: legacy });
      localStorage.removeItem(TOKEN_KEY);
    } catch (e) { /* keep using it from memory this session */ }
  }
}

// Resolves once the token has been read out of secure storage. `api()` awaits
// this before every request: it settles once, and without it the first call
// after a cold start would race the (asynchronous) Keychain read and go out
// unauthenticated.
export function tokenReady() {
  if (!API_BASE) return Promise.resolve();
  if (!readyPromise) readyPromise = loadToken();
  return readyPromise;
}

export function storedToken() {
  if (!API_BASE) return null;
  return memoryToken;
}

export function storeToken(token) {
  if (!API_BASE) return Promise.resolve();
  memoryToken = token || null;
  const secure = securePlugin();
  if (secure) {
    // Never leave a copy behind in the clear.
    try { localStorage.removeItem(TOKEN_KEY); } catch (e) { /* ignore */ }
    if (token) {
      // A Keychain WRITE that fails costs this session's persistence, not
      // the session: the in-memory copy is already live, and the next launch
      // simply asks the seller to sign in again.
      return Promise.resolve(secure.set({ key: TOKEN_KEY, value: token }))
        .catch(() => {});
    }
    // A removal is the other way round. The token is a self-contained JWT
    // good for 30 days, and loadToken() reads it back on the next cold
    // start — so a remove that quietly failed means the app reopens signed
    // in as the person who just signed out. On a shared phone that is
    // somebody else's store, and the only symptom is that logging out did
    // not work, discovered on the next launch rather than at the time.
    //
    // So a failed removal falls back to the operation already known to work:
    // overwrite the entry with an empty value. loadToken treats a falsy
    // value as "nothing stored", which turns an unremovable credential into
    // an unusable one. If even that fails there is nothing further to try
    // from here, and it must still not raise into the logout that called it.
    return Promise.resolve(secure.remove({ key: TOKEN_KEY }))
      .catch(() => Promise.resolve(secure.set({ key: TOKEN_KEY, value: "" })))
      .catch(() => {});
  }
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch (e) { /* private mode — session lasts until the app closes */ }
  return Promise.resolve();
}

// "ios", "android", or "web" (the browser, and the tests).
export function nativePlatform() {
  try {
    return isNative() ? window.Capacitor.getPlatform?.() || "web" : "web";
  } catch (e) {
    return "web";
  }
}

// --- external browser -------------------------------------------------------
// Two ways out of the app, for two reasons:
//
// * A page the seller is shown (an eBay listing, Seller Hub, the privacy
//   policy) opens in the in-app browser — Safari's view controller on iOS, a
//   Custom Tab on Android — via the Browser plugin. Closing it is back in the
//   app. Never the app's own webview: on Android a link to an allowed host
//   (*.ebay.com, which the OAuth flow needs) would REPLACE the app.
//
// * A purchase (`{ system: true }`) leaves for the SYSTEM browser. Completing
//   a non-Apple checkout inside the app is a guideline 3.1.1 rejection; a link
//   out is allowed on the US storefront. window.open is that link in the
//   shell: iOS hands it to Safari, and Android to the default browser because
//   the checkout host is not one the webview may load.
//
// Returns false when nothing could open, so the caller says so instead of
// falling back to in-webview navigation — an error message beats a rejected
// binary.

// window.open as the platform made it. The native shell routes app calls to
// window.open through openExternal (see nativeShell.js) and keeps the
// original here, so a purchase can still reach the system browser.
function systemOpen() {
  return window.open?.native || window.open;
}

export async function openExternal(url, { system = false } = {}) {
  const open = systemOpen();
  if (system && isNative()) {
    try {
      // The shell answers window.open with no window even when Safari or the
      // default browser opened, so null is not a failure here; a throw is.
      open.call(window, url, "_blank");
      return true;
    } catch (e) {
      return false;
    }
  }
  if (!system) {
    try {
      const browser = window.Capacitor?.Plugins?.Browser;
      if (browser?.open) {
        await browser.open({ url });
        return true;
      }
    } catch (e) { /* fall through */ }
  }
  try {
    const w = open.call(window, url, "_blank");
    if (w) return true;
  } catch (e) { /* fall through */ }
  return false;
}

// Whether this build may offer token packs. Tokens are digital goods used in
// the app, which the stores normally require to be sold through their own
// billing; the app sells them through Stripe on the web instead.
//   iOS: allowed as a link out on the US storefront (App Store availability
//        is set to the US for this — see MOBILE.md).
//   Android: Google Play allows a link out only under its external-content-
//        links program, so the Android build offers no purchase at all unless
//        it was built for that (VITE_ANDROID_EXTERNAL_PURCHASES=1).
// Billing itself is a server flag; this is decided in the binary so that
// switching billing on cannot change what an approved build shows.
export function tokenPurchasesAllowed() {
  if (nativePlatform() !== "android") return true;
  return import.meta.env.VITE_ANDROID_EXTERNAL_PURCHASES === "1";
}

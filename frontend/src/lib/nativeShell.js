/* What the app does differently inside the native shell — and only there.
 *
 * The web build runs in a browser, which already knows what a link in a new
 * tab is and what the back button means. The Capacitor shell is one webview
 * with neither, so this file supplies them. Every piece is a no-op on the web.
 *
 * Links. A link that opens a new tab (target="_blank") or leaves for another
 * site, and any window.open from app code, goes to the in-app browser through
 * openExternal. In the webview itself it would do the wrong thing: on Android
 * a link to *.ebay.com — a host the webview must be allowed for eBay's sign-in
 * — loads in place and REPLACES the app, with nothing to come back to. A
 * click a component already handled (SiteLink calls preventDefault) is left
 * alone, and eBay's sign-in is a location change, not a link, so it still
 * runs in the webview as the OAuth flow needs.
 *
 * Back (Android). An open dialog closes first, through the same Escape path
 * as a keyboard — so a dialog that refuses to close mid-task refuses here
 * too. Then the screen's own back, newest and highest priority first (the
 * editor's "close this listing", which saves a draft or asks about a live
 * one). Then the app's: back to the dashboard. With nowhere left to go, the
 * app goes to the background, as Android apps do — never quit from the
 * middle of a screen.
 */
import { useEffect, useRef } from "react";
import { isNative, nativePlatform, openExternal } from "@/lib/platform";

const handlers = []; // { run, priority }

/** Register a back-button handler: `run()` returns true when it handled the
 *  press. Higher priority runs first; among equals, the newest. Returns the
 *  function that unregisters it. */
export function onBackButton(run, priority = 0) {
  const entry = { run, priority };
  handlers.push(entry);
  return () => {
    const i = handlers.indexOf(entry);
    if (i >= 0) handlers.splice(i, 1);
  };
}

/** onBackButton for a component's lifetime, always calling its latest
 *  `run` (so the handler can close over state without re-registering). */
export function useNativeBack(run, priority = 0) {
  const latest = useRef(run);
  useEffect(() => { latest.current = run; });
  useEffect(() => onBackButton(() => latest.current(), priority), [priority]);
}

/** One back press. True when something in the app took it. */
export function handleBack(doc = document) {
  if (doc.querySelector('[role="dialog"]')) {
    doc.defaultView.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    return true;
  }
  const ordered = handlers
    .map((h, i) => ({ ...h, i }))
    .sort((a, b) => b.priority - a.priority || b.i - a.i);
  return ordered.some((h) => h.run() === true);
}

/** The absolute http(s) URL a link leaves the app for, or null when the
 *  link stays inside it. */
export function leavingUrl(href, { target = "", origin }) {
  let url;
  try {
    url = new URL(href, origin);
  } catch (e) {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (target === "_blank" || url.origin !== origin) return url.href;
  return null;
}

/** Wire the shell's behaviour into the page. Call once, before the first
 *  render. Returns an uninstaller (for tests). */
export function installNativeShell(win = window) {
  if (!isNative()) return () => {};
  const doc = win.document;
  const origin = win.location.origin;

  const onClick = (e) => {
    if (e.defaultPrevented || e.button !== 0) return;
    const link = e.target?.closest?.("a[href]");
    if (!link) return;
    const url = leavingUrl(link.getAttribute("href"), { target: link.target, origin });
    if (!url) return;
    e.preventDefault();
    openExternal(url);
  };
  doc.addEventListener("click", onClick);

  const original = win.open;
  const routed = (href, ...rest) => {
    const url = href && leavingUrl(String(href), { target: "_blank", origin });
    if (!url) return original.call(win, href, ...rest);
    openExternal(url);
    return null;
  };
  // openExternal({ system: true }) still needs the real one for a purchase.
  routed.native = original;
  win.open = routed;

  let listener = null;
  const app = win.Capacitor?.Plugins?.App;
  if (nativePlatform() === "android" && app?.addListener) {
    listener = Promise.resolve(app.addListener("backButton", () => {
      if (!handleBack(doc)) app.minimizeApp?.();
    })).catch(() => null);
  }

  return () => {
    doc.removeEventListener("click", onClick);
    win.open = original;
    listener?.then((l) => l?.remove?.());
  };
}

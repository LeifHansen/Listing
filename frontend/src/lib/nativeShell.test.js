/* The native shell's links, back button and purchase guard.
 *
 * None of this runs on the web, and none of it can be clicked through in CI:
 * the smoke walk drives a browser, not the Capacitor webview. So the rules are
 * pinned here against a stand-in for the runtime the shell injects
 * (window.Capacitor, with the App and Browser plugins).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let uninstall = () => {};

function shell(platform, plugins = {}) {
  globalThis.window.Capacitor = {
    isNativePlatform: () => true,
    getPlatform: () => platform,
    Plugins: plugins,
  };
}

async function fresh() {
  vi.resetModules();
  const nativeShell = await import("./nativeShell");
  const platform = await import("./platform");
  return { ...nativeShell, ...platform };
}

// Whether the click was taken from the webview: read at the window, after
// every handler below it ran, and then cancelled so jsdom doesn't try to
// navigate.
function click(el) {
  let prevented;
  const stop = (e) => { prevented = e.defaultPrevented; e.preventDefault(); };
  window.addEventListener("click", stop);
  el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
  window.removeEventListener("click", stop);
  return prevented;
}

function link(href, target = "") {
  const a = document.createElement("a");
  a.setAttribute("href", href);
  if (target) a.target = target;
  document.body.appendChild(a);
  return a;
}

const realOpen = window.open;

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(() => {
  uninstall();
  uninstall = () => {};
  window.open = realOpen;
  delete globalThis.window.Capacitor;
  vi.unstubAllEnvs();
});

describe("on the web", () => {
  it("changes nothing", async () => {
    const { installNativeShell } = await fresh();
    uninstall = installNativeShell();
    expect(window.open).toBe(realOpen);
    expect(click(link("https://www.ebay.com/itm/1", "_blank"))).toBe(false);
  });

  it("may sell tokens", async () => {
    const { tokenPurchasesAllowed } = await fresh();
    expect(tokenPurchasesAllowed()).toBe(true);
  });
});

describe("links in the shell", () => {
  let browser;

  beforeEach(async () => {
    browser = { open: vi.fn(async () => {}) };
    shell("android", { Browser: browser });
  });

  it("an eBay link opens in the in-app browser, never in place of the app", async () => {
    const { installNativeShell } = await fresh();
    uninstall = installNativeShell();
    expect(click(link("https://www.ebay.com/itm/123", "_blank"))).toBe(true);
    expect(browser.open).toHaveBeenCalledWith({ url: "https://www.ebay.com/itm/123" });
  });

  it("a link off the app's origin leaves even without target=_blank", async () => {
    const { installNativeShell } = await fresh();
    uninstall = installNativeShell();
    expect(click(link("https://www.ebay.com/sh/ovw"))).toBe(true);
  });

  it("an in-app link, a mailto, and a click a component handled are left alone", async () => {
    const { installNativeShell } = await fresh();
    uninstall = installNativeShell();
    expect(click(link("/privacy-policy.html"))).toBe(false);
    expect(click(link("mailto:help@example.com", "_blank"))).toBe(false);
    const handled = link("https://www.ebay.com/itm/9", "_blank");
    handled.addEventListener("click", (e) => e.preventDefault());
    click(handled);
    expect(browser.open).not.toHaveBeenCalled();
  });

  it("window.open from app code goes to the in-app browser too", async () => {
    const { installNativeShell } = await fresh();
    window.open = vi.fn(() => null);
    const original = window.open;
    uninstall = installNativeShell();
    expect(window.open("https://www.ebay.com/itm/5", "_blank", "noopener")).toBeNull();
    expect(browser.open).toHaveBeenCalledWith({ url: "https://www.ebay.com/itm/5" });
    expect(original).not.toHaveBeenCalled();
  });

  it("a purchase leaves for the SYSTEM browser, not the in-app one", async () => {
    const { installNativeShell, openExternal } = await fresh();
    // The shell's window.open answers null even when Safari opened.
    window.open = vi.fn(() => null);
    const original = window.open;
    uninstall = installNativeShell();
    await expect(openExternal("https://checkout.stripe.com/c/1", { system: true })).resolves.toBe(true);
    expect(original).toHaveBeenCalledWith("https://checkout.stripe.com/c/1", "_blank");
    expect(browser.open).not.toHaveBeenCalled();
  });
});

describe("Android's back button", () => {
  let app;
  let press;

  beforeEach(() => {
    app = {
      addListener: vi.fn((name, fn) => { if (name === "backButton") press = fn; return { remove() {} }; }),
      minimizeApp: vi.fn(),
    };
    shell("android", { App: app });
  });

  it("closes an open dialog first, through its own Escape", async () => {
    const { installNativeShell, onBackButton } = await fresh();
    uninstall = installNativeShell();
    const screen = vi.fn(() => true);
    const off = onBackButton(screen, 10);
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    document.body.appendChild(dialog);
    const keys = [];
    const onKey = (e) => keys.push(e.key);
    window.addEventListener("keydown", onKey);
    press();
    window.removeEventListener("keydown", onKey);
    off();
    expect(keys).toEqual(["Escape"]);
    expect(screen).not.toHaveBeenCalled();
    expect(app.minimizeApp).not.toHaveBeenCalled();
  });

  it("then the open listing's own Back, ahead of the app's", async () => {
    const { installNativeShell, onBackButton } = await fresh();
    uninstall = installNativeShell();
    const order = [];
    // The app's handler registers AFTER the editor's (React runs a parent's
    // effects after its child's); priority, not order, puts the editor first.
    const offEditor = onBackButton(() => { order.push("editor"); return true; }, 10);
    const offApp = onBackButton(() => { order.push("app"); return true; });
    press();
    offEditor();
    press();
    offApp();
    expect(order).toEqual(["editor", "app"]);
  });

  it("sends the app to the background when nothing is left to go back to", async () => {
    const { installNativeShell, onBackButton } = await fresh();
    uninstall = installNativeShell();
    const off = onBackButton(() => false);
    press();
    off();
    expect(app.minimizeApp).toHaveBeenCalledTimes(1);
  });

  it("is not wired on iOS, which has no back button", async () => {
    shell("ios", { App: app });
    const { installNativeShell } = await fresh();
    uninstall = installNativeShell();
    expect(app.addListener).not.toHaveBeenCalled();
  });
});

describe("selling tokens from the app", () => {
  it("the Android build offers no purchase unless built for Play's link-out program", async () => {
    shell("android");
    let { tokenPurchasesAllowed } = await fresh();
    expect(tokenPurchasesAllowed()).toBe(false);
    vi.stubEnv("VITE_ANDROID_EXTERNAL_PURCHASES", "1");
    ({ tokenPurchasesAllowed } = await fresh());
    expect(tokenPurchasesAllowed()).toBe(true);
  });

  it("the iOS build links out (US storefront)", async () => {
    shell("ios");
    const { tokenPurchasesAllowed } = await fresh();
    expect(tokenPurchasesAllowed()).toBe(true);
  });

  it("the out-of-tokens sentence loses its invitation to buy, and keeps the rest", async () => {
    const { withoutPurchasePrompt } = await import("./api");
    // services/tokens.insufficient_message, verbatim.
    expect(withoutPurchasePrompt(
      "Out of AI tokens (0 left). Your free 50 reset on Dec 1 — or buy a token pack to keep going now.",
    )).toBe("Out of AI tokens (0 left). Your free 50 reset on Dec 1.");
  });
});

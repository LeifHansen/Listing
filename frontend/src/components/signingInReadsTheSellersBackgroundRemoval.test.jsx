/* Signing in reads the background-removal switch of the seller who signed in.
 *
 * /api/bg-removal/status answers per seller: whether they switched cloud
 * removal on, and how much of the month they have used. The store read it at
 * boot and on sign-out -- both times as nobody -- and signing in through the
 * dialog re-read only the eBay status. So for the rest of that session a
 * seller who HAD switched it on was treated as one who had not: the uploader
 * told them to "Turn on background removal in Settings first", Settings
 * offered to turn it on, and the device queue never handed Photoroom a photo
 * it could not cut. A reload fixed it, which is why it looked intermittent.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { AuthDialog } from "@/components/AuthDialog";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let signedIn;
const bgReads = [];

function reply(body, status = 200) {
  return Promise.resolve({
    ok: status < 400, status, headers: { get: () => null },
    json: () => Promise.resolve(body),
  });
}

function respond(path) {
  if (path.startsWith("/api/bg-removal/status")) {
    bgReads.push(signedIn);
    // Cloud removal is available on the server; THIS seller switched it on.
    return reply({ available: true, needs_activation: true, active: signedIn,
                   used: signedIn ? 3 : 0, limit: 100 });
  }
  if (path.startsWith("/api/auth/login")) {
    signedIn = true;
    return reply({ user: { id: 7, email: "a@example.com" }, token: "t" });
  }
  if (path.startsWith("/api/auth/me")) return reply({ user: null });
  if (path.startsWith("/api/listings")) {
    return reply({ authed: signedIn, db: { configured: true }, listings: [] });
  }
  if (path.startsWith("/api/tokens")) return reply({ enabled: false, total: 0, packs: [], costs: {} });
  return reply({ detail: "Not found" }, 404);
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

function type(el, value) {
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  set.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => {
  signedIn = false;
  bgReads.length = 0;
  vi.stubGlobal("fetch", vi.fn((url) => respond(String(url))));
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("signing in through the dialog", () => {
  it("reads background removal again, as the seller who signed in", async () => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    let app = null;
    await act(async () => {
      root.render(
        <ToastProvider>
          <AppProvider>
            <Probe onValue={(v) => { app = v; }} />
            <AuthDialog />
          </AppProvider>
        </ToastProvider>,
      );
    });
    await settle();
    expect(app.bgRemoval.active).toBe(false);   // read as nobody at boot

    await act(async () => { app.setAuthOpen(true); });
    await settle();
    await act(async () => {
      type(document.querySelector('input[type="email"]'), "a@example.com");
      type(document.querySelector('input[type="password"]'), "correct horse");
    });
    const logIn = [...document.querySelectorAll("button")]
      .find((b) => b.textContent.trim() === "Log in" && b.closest('[role="dialog"]')
        && !b.getAttribute("role"));
    await act(async () => { logIn.click(); });
    await settle();
    await settle();

    expect(app.user?.id).toBe(7);
    expect(bgReads.at(-1)).toBe(true);
    expect(app.bgRemoval.active).toBe(true);
    expect(app.bgRemoval.used).toBe(3);
    await act(async () => { root.unmount(); });
  });
});

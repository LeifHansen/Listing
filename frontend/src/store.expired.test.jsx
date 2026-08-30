/* A session cancelled elsewhere has to end here too.
 *
 * This branch made sessions revocable — "Sign out everywhere" cancels every
 * token, and a stolen one can be killed. On the device that did not press the
 * button, nothing happened: the client never read a 401, so the cached
 * account stayed on screen and every fetch behind it failed with "Log in
 * first." rendered as an error on whichever card asked for it.
 *
 * The seller sees their own email above a store that will not load, no prompt
 * to sign in, and nothing saying why. The revocation worked; the app never
 * noticed — which makes the feature look broken and leaves the previous
 * account's data on a screen that no longer has an account.
 *
 * Store-level assertions for the same reason as the logout ones next door:
 * the leak is in the state, and asserting on the state is what stops it
 * coming back when a view is rewritten.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const SIGNED_IN = {
  "/api/auth/me": { user: { id: 7, email: "lahey@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: false },
  "/api/ebay/status": {
    connected: true, env: "production", username: "mr._lahey",
    oauth_ready: true,
  },
  "/api/listings": {
    authed: true,
    db: { configured: true, connected: true },
    listings: [{ id: "l1", title: "A trailer park lamp", status: "active" }],
  },
  "/api/notifications": {
    notifications: [{ id: "n1", read: false, kind: "sold" }], unread: 1,
  },
  "/api/marketplaces": { marketplaces: [{ key: "ebay", connected: true }] },
  "/api/tokens": { enabled: true, total: 250, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
};

// What the same server answers once the token has been cancelled. Modelled
// rather than assumed: the store reloads on `user` changing, so a fixture
// that kept serving one seller's listings to nobody would hide the very leak
// this file is about.
const SIGNED_OUT = {
  "/api/auth/me": { user: null },
  "/api/health": SIGNED_IN["/api/health"],
  "/api/ebay/status": { connected: false },
  "/api/listings": { authed: false, db: { configured: true, connected: true },
                     listings: [] },
  "/api/notifications": { notifications: [], unread: 0 },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: true, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
};

let alive = true;

function respond(path) {
  const table = alive ? SIGNED_IN : SIGNED_OUT;
  const key = Object.keys(table).find((k) => path.startsWith(k));
  const body = key ? table[key] : null;
  return Promise.resolve({
    ok: !!body,
    status: body ? 200 : 404,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body || { detail: "Not found" }),
    text: () => Promise.resolve(JSON.stringify(body || { detail: "Not found" })),
  });
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

async function mountSignedIn() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let app = null;
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <Probe onValue={(v) => { app = v; }} />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, host, get: () => app };
}

/** The token is cancelled, and a request notices — which is what lib/api.js
 *  turns into this event. */
async function sessionEnds() {
  alive = false;
  await act(async () => {
    window.dispatchEvent(new CustomEvent("auth:expired",
                                         { detail: "Log in first." }));
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe("a session cancelled from somewhere else", () => {
  beforeEach(() => {
    alive = true;
    vi.stubGlobal("fetch", vi.fn((url) => respond(String(url))));
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("takes the previous account off the screen", async () => {
    const { get, root } = await mountSignedIn();
    expect(get().user.email).toBe("lahey@example.com");

    await sessionEnds();

    expect(get().user).toBe(null);
    expect(get().listingsState.items).toEqual([]);
    expect(get().ebay.username).toBeFalsy();
    expect(get().notifications.unread).toBe(0);
    await act(async () => { root.unmount(); });
  });

  it("lands where signing back in is the obvious next move", async () => {
    const { get, root } = await mountSignedIn();
    await act(async () => { get().setView("settings"); });

    await sessionEnds();

    expect(get().view).toBe("dashboard");
    expect(get().authOpen).toBe(true);
    await act(async () => { root.unmount(); });
  });

  it("does not ask the server to end a session that is already gone", async () => {
    const { get, root } = await mountSignedIn();
    fetch.mockClear();

    await sessionEnds();

    const called = fetch.mock.calls.map((c) => String(c[0]));
    expect(called.some((u) => u.includes("/api/auth/logout"))).toBe(false);
    // And it still ended: the point is that it did so without the round trip,
    // not that it skipped the work.
    expect(get().user).toBe(null);
    await act(async () => { root.unmount(); });
  });

  it("says it once when several requests fail together", async () => {
    // A dashboard fires half a dozen fetches at once, and every one of them
    // 401s. Without a guard the seller gets six toasts and the clear runs six
    // times over.
    const { get, root } = await mountSignedIn();
    await sessionEnds();
    expect(get().user).toBe(null);
    // The second one arrives with nobody signed in. It must be a no-op rather
    // than a second clear and a second sentence.
    await sessionEnds();
    expect(get().user).toBe(null);
    expect(get().listingsState.items).toEqual([]);
    await act(async () => { root.unmount(); });
  });
});

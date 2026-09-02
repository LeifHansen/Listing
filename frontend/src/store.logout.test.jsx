/* Logging out has to end the session, not just the greeting.
 *
 * The bug this covers was reported twice, in two shapes. "When I log out,
 * redirect to the login page, not stay in the dashboard" was the visible half:
 * logout() cleared `user` and nothing else, and since the app has no route gate
 * (every view renders whether or not anyone is signed in), the seller stayed
 * exactly where they were. The other half was worse and quieter — the previous
 * account's listings, sold alerts, eBay username and token balance were all
 * still in memory, so they stayed on screen too, on a dashboard that no longer
 * had a user. On a shared machine that is someone else's store.
 *
 * These are store-level assertions on purpose: the leak is in the state, and
 * asserting on the state is what stops it coming back when a view is rewritten.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";

// Tells React that act() is legitimate here, so effects flush inside it
// instead of warning and running late.
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// One signed-in seller's worth of server answers. Anything not listed here
// 404s, which the store treats as "keep what you have" — the same as prod.
const SIGNED_IN = {
  "/api/auth/me": { user: { id: 7, email: "lahey@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: false },
  "/api/ebay/status": {
    connected: true, env: "production", username: "mr._lahey",
    oauth_ready: true, foreign_listings: 3, messaging_enabled: true,
  },
  "/api/listings": {
    authed: true,
    db: { configured: true, connected: true },
    listings: [{ id: "l1", title: "A trailer park lamp", status: "active" }],
  },
  "/api/notifications": {
    notifications: [{ id: "n1", read: false, kind: "sold" }], unread: 1,
  },
  "/api/messages": {
    conversations: [{ id: "ebay:c1", raw_id: "c1", marketplace: "ebay",
                      counterparty: "sarah_m", snippet: "Still available?",
                      last_at: "2026-08-30T09:00:00Z", unread: 2 }],
    unread: 2, available: true, reason: "",
    sources: [{ key: "ebay", label: "eBay", available: true, unread: 2,
                supported: true, reason: "", message: "" }],
  },
  "/api/marketplaces": { marketplaces: [{ key: "ebay", connected: true }] },
  "/api/tokens": { enabled: true, total: 250, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
  "/api/auth/logout": { ok: true },
};

function respond(path) {
  const key = Object.keys(SIGNED_IN).find((k) => path.startsWith(k));
  const body = key ? SIGNED_IN[key] : null;
  return Promise.resolve({
    ok: !!body,
    status: body ? 200 : 404,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body || { detail: "Not found" }),
    text: () => Promise.resolve(JSON.stringify(body || { detail: "Not found" })),
  });
}

// Hands the live context object back to the test. Reading it through a ref-ish
// closure rather than rendering assertions keeps the test about the store.
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
  // Let the boot fetches (auth, health, eBay, listings, marketplaces...) settle.
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, host, get: () => app };
}

describe("logout", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn((url) => respond(String(url))));
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("loads one seller's session first (the fixture is doing its job)", async () => {
    const { get, root } = await mountSignedIn();
    expect(get().user.email).toBe("lahey@example.com");
    expect(get().listingsState.items).toHaveLength(1);
    expect(get().ebay.username).toBe("mr._lahey");
    expect(get().notifications.unread).toBe(1);
    expect(get().messages.unread).toBe(2);
    expect(get().tokens.total).toBe(250);
    await act(async () => { root.unmount(); });
  });

  it("sends the seller back to a signed-out dashboard with the login prompt open",
    async () => {
      const { get, root } = await mountSignedIn();
      // Somewhere other than the dashboard, which is where the bug was visible:
      // logging out from the Sell screen left you in an editor you had no
      // account for.
      await act(async () => { get().setView("settings"); });
      expect(get().view).toBe("settings");

      await act(async () => { await get().logout(); });

      expect(get().user).toBe(null);
      expect(get().view).toBe("dashboard");
      expect(get().authOpen).toBe(true);
      await act(async () => { root.unmount(); });
    });

  it("takes the whole session with it, in the same commit", async () => {
    const { get, root } = await mountSignedIn();
    localStorage.setItem("quickflip-bulk", JSON.stringify({ jobId: "job-1" }));
    await act(async () => { get().startBulk("job-1"); });
    expect(get().activeBulk).toEqual({ jobId: "job-1" });

    // The refetches logout triggers must not be what does the clearing, so
    // freeze every one of them: past this point the sign-out POST is the only
    // request that can still answer. Anything left populated below is therefore
    // state logout failed to clear, not state a response would have replaced.
    fetch.mockImplementation((url) => (
      String(url).startsWith("/api/auth/logout")
        ? respond("/api/auth/logout")
        : new Promise(() => {})));
    await act(async () => { await get().logout(); });

    const app = get();
    expect(app.user).toBe(null);
    expect(app.listingsState.items).toEqual([]);
    // Everything the previous account's bell held is gone. `checked` rides
    // along on this state now (it says whether the last read landed); a
    // signed-out bell has nothing to report and is not an outage, so it
    // resets to true with the rest.
    expect(app.notifications).toEqual({ items: [], unread: 0, checked: true });
    expect(app.messages.conversations).toEqual([]);
    expect(app.messages.unread).toBe(0);
    expect(app.threads).toEqual({});
    expect(app.activeConversationId).toBe(null);
    expect(app.ebay.connected).toBe(false);
    expect(app.ebay.username).toBe("");
    expect(app.ebay.foreign_listings).toBe(0);
    expect(app.marketplaces).toEqual([]);
    expect(app.connectedMarketplaces).toEqual([]);
    expect(app.tokens.total).toBe(0);
    expect(app.session).toBe(null);
    expect(app.policiesData).toBe(null);
    expect(app.metricsById).toEqual({});
    expect(app.storeSync.lastSynced).toBe(null);
    expect(app.activeBulk).toBe(null);
    expect(app.bulkRetry).toBe(null);
    expect(localStorage.getItem("thryft-bulk")).toBe(null);
    // The pre-rename key too: a seller who signs out on the same browser they
    // used before the rename must not leave the old copy behind for whoever
    // signs in next. Setting it above is what makes this assertion mean
    // something — it is a real leftover, cleared by the same call.
    expect(localStorage.getItem("quickflip-bulk")).toBe(null);
    expect(app.skippedDraftIds.size).toBe(0);
    await act(async () => { root.unmount(); });
  });

  it("clears the session even when the logout request itself fails", async () => {
    const { get, root } = await mountSignedIn();
    fetch.mockImplementation(() => Promise.reject(new Error("offline")));
    await act(async () => { await get().logout(); });

    // A server we could not reach is not a reason to keep showing an account
    // its owner has asked to leave.
    expect(get().user).toBe(null);
    expect(get().listingsState.items).toEqual([]);
    expect(get().authOpen).toBe(true);
    await act(async () => { root.unmount(); });
  });
});

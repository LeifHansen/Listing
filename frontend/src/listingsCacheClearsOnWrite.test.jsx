/* The listings cache is cleared by the writes that make it wrong.
 *
 * Every screen in the app — Dashboard, Listings, the drafts strip, the tab
 * counts — renders from one cached array in the store, and nothing re-reads
 * the server on its own. So a write that wasn't followed by a refetch left
 * the app showing what the store USED to be: a photo still sideways on its
 * card after a rotate, a draft under the title it had before it was renamed,
 * a listing just created missing from Drafts entirely until some unrelated
 * refresh happened along.
 *
 * `invalidateListings` is the ordinary rule every data-backed site follows —
 * a mutation invalidates the cache — with two properties worth pinning: a
 * burst of small writes costs ONE refetch, and a tab that has been sitting in
 * the background re-reads when it comes back, but only once what it holds has
 * actually gone stale.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: false },
  "/api/ebay/policies": { policies: [] },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
};

function json(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

async function mount() {
  const state = { listings: [], loads: 0 };
  vi.stubGlobal("fetch", vi.fn((url) => {
    const path = String(url);
    if (path.startsWith("/api/listings")) {
      state.loads += 1;
      return json({ authed: true, db: { configured: true, connected: true },
                    listings: state.listings });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({});
  }));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let app = null;
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider><Probe onValue={(v) => { app = v; }} /></AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  return {
    root,
    state,
    app: () => app,
    tick: async (ms) => {
      await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
    },
  };
}

function setHidden(hidden) {
  Object.defineProperty(document, "hidden", {
    configurable: true, get: () => hidden,
  });
}

describe("the listings cache is cleared by the writes that make it wrong", () => {
  beforeEach(() => { localStorage.clear(); vi.useFakeTimers(); setHidden(false); });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("re-reads the store after a write says something changed", async () => {
    const ui = await mount();
    const before = ui.state.loads;
    // What the server holds after the edit the caller just made.
    ui.state.listings = [{ id: "d1", status: "draft",
                           listing: { title: "Enamel Coffee Pot" } }];

    await act(async () => { ui.app().invalidateListings(); });
    await ui.tick(500);

    expect(ui.state.loads).toBe(before + 1);
    expect(ui.app().listingsState.items).toHaveLength(1);

    await act(async () => { ui.root.unmount(); });
  });

  it("costs one refetch for a burst of writes, not one each", async () => {
    // Dragging three photos into place, then saving, is one change as far as
    // any card is concerned.
    const ui = await mount();
    const before = ui.state.loads;

    await act(async () => {
      ui.app().invalidateListings();
      ui.app().invalidateListings();
      ui.app().invalidateListings();
    });
    await ui.tick(500);

    expect(ui.state.loads).toBe(before + 1);

    await act(async () => { ui.root.unmount(); });
  });

  it("re-reads when a backgrounded tab comes back to something stale", async () => {
    const ui = await mount();
    const before = ui.state.loads;

    // Long enough that what the tab holds is a snapshot, not the store.
    await ui.tick(90000);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await ui.tick(50);

    expect(ui.state.loads).toBe(before + 1);

    await act(async () => { ui.root.unmount(); });
  });

  it("does not refetch on every glance at a tab it just read", async () => {
    const ui = await mount();
    const before = ui.state.loads;

    await ui.tick(1000);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("focus"));
    });
    await ui.tick(50);

    expect(ui.state.loads).toBe(before);

    await act(async () => { ui.root.unmount(); });
  });

  it("stays quiet while the tab is hidden", async () => {
    const ui = await mount();
    const before = ui.state.loads;
    await ui.tick(90000);

    setHidden(true);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await ui.tick(50);

    expect(ui.state.loads).toBe(before);

    await act(async () => { ui.root.unmount(); });
  });
});

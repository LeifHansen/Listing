/* An ended listing does not sit on the grid waiting to be trashed.
 *
 * What the seller was looking at: an "Ended" card among their live ones, a
 * broken-image tile where the photo should be (eBay stops serving the photos
 * of an item that finished a while ago), a "Relist" link, and a trash button
 * they were expected to press — one per listing that ever ended. Their report
 * was three words: these should be removed automatically.
 *
 * So two things have to be true on this screen, and neither was:
 *
 *  1. Ending a listing takes its card away. It used to move to an "Inactive"
 *     tab, which is where the pile came from.
 *  2. A card for a listing that ended on EBAY's side goes on its own, with
 *     nothing clicked. The store sync sweeps those records; this pins that
 *     the screen actually reloads on the sweep, because a removal the client
 *     never re-reads leaves the same card sitting there until a refresh.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, markAutoSynced, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { ListingsView } from "@/views/ListingsView";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: true },
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

const live = (id, title) => ({
  id, status: "published", updated_at: "2026-09-01T00:00:00Z",
  listing: { title, price: 34, source: "ebay", ebay_listing_id: `1${id}`,
             image_urls: ["https://i.ebayimg.com/x.jpg"] },
});

const ended = (id, title) => ({
  ...live(id, title), status: "ended", listing: { title, price: 34 },
});

/* The server, holding the seller's rows. `sweep` is what
   /api/ebay/sync-listings answers — the real one removes ended records and
   reports how many went. */
function server(state) {
  return (url, opts = {}) => {
    const path = String(url);
    if (path.startsWith("/api/listings")) {
      state.loads += 1;
      return json({ authed: true, db: { configured: true, connected: true },
                    listings: state.listings });
    }
    if (path === "/api/ebay/end-listing") {
      const id = JSON.parse(opts.body || "{}").session_id;
      // What the server does now: the listing comes off eBay and the record
      // goes with it, so the refresh that follows must not hand it back.
      state.listings = state.listings.filter((l) => l.id !== id);
      return json({ ended: true, removed: true, status: "ended" });
    }
    if (path === "/api/ebay/sync-listings") {
      const before = state.listings.length;
      state.listings = state.listings.filter((l) => l.status !== "ended");
      return json({ checked: 0, changed: 0,
                    removed: before - state.listings.length });
    }
    if (path === "/api/ebay/import-listings") return json({ imported: 0 });
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({});
  };
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

async function mount(listings) {
  const state = { listings, loads: 0 };
  vi.stubGlobal("fetch", vi.fn(server(state)));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let app = null;
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <Probe onValue={(v) => { app = v; }} />
          <ListingsView />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  return {
    root, host, state, app: () => app,
    text: () => host.textContent || "",
    settle: async (ms = 10) => {
      await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
    },
  };
}

describe("ending a listing from its card", () => {
  beforeEach(() => { localStorage.clear(); vi.useFakeTimers(); });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("takes the card off the grid instead of filing it under a tab", async () => {
    const ui = await mount([live("l1", "Levi's 527 Boot Cut"),
                            live("l2", "Buc-ee's Tie-Dye Tee")]);
    expect(ui.text()).toContain("Buc-ee's Tie-Dye Tee");

    // The End control that belongs to THAT card: every card has one, and
    // the innermost wrapper holding both the title and a ⊘ is the card.
    const card = [...ui.host.querySelectorAll("div")].filter(
      (d) => (d.textContent || "").includes("Buc-ee's Tie-Dye Tee")
        && d.querySelector("button[aria-label='End listing on eBay']")).pop();
    const end = card.querySelector("button[aria-label='End listing on eBay']");
    expect(end).toBeTruthy();
    await act(async () => { end.click(); });
    // The dialog says what it is about to do: this is a removal, not a move.
    const dialog = document.querySelector("[role='dialog']");
    expect(dialog.textContent).toContain("removed");
    const confirm = [...dialog.querySelectorAll("button")]
      .find((b) => (b.textContent || "").trim() === "End & remove");
    await act(async () => { confirm.click(); });
    await ui.settle();

    expect(ui.app().listingsState.items.map((i) => i.id)).toEqual(["l1"]);
    expect(ui.text()).not.toContain("Buc-ee's Tie-Dye Tee");
    await act(async () => { ui.root.unmount(); });
  });

  it("has no tab left that would show an ended listing", async () => {
    // The archive is sales only now. A record still stored as ended (an old
    // one, before the sweep reaches it) must not reappear under a tab that
    // says "Sold".
    const ui = await mount([ended("e1", "Ended long ago"),
                            { ...live("s1", "Sold one"), status: "sold" }]);
    await act(async () => { ui.app().setListingsTab("inactive"); });
    expect(ui.text()).toContain("Sold one");
    expect(ui.text()).not.toContain("Ended long ago");
    await act(async () => { ui.root.unmount(); });
  });
});

describe("a listing that ended on eBay's side", () => {
  beforeEach(() => { localStorage.clear(); vi.useFakeTimers(); });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("leaves the grid on the quiet re-check, with nothing clicked", async () => {
    // The store mirror was rebuilt recently, so no import runs on mount and
    // nothing else reloads the list. What is left is the half-hourly status
    // re-check — the pass that actually sweeps ended records — and it is the
    // only thing that can take this card off the screen.
    markAutoSynced(7);
    const ui = await mount([live("l1", "Levi's 527 Boot Cut"),
                            ended("e1", "Buc-ee's Tie-Dye Tee")]);
    // Under "All", which is where the seller was looking at it: the archive
    // subtracts sales, not endings, so an ended card sat among the live ones.
    await act(async () => { ui.app().setListingsTab("all"); });
    expect(ui.text()).toContain("Buc-ee's Tie-Dye Tee");
    expect(ui.state.listings).toHaveLength(2);

    await ui.settle(31 * 60000);

    // The sweep removed the row; the point being pinned is that the screen
    // re-read it. `removed` and `changed` are separate counts, and reloading
    // on `changed` alone left the card exactly where it was.
    expect(ui.state.listings.map((l) => l.id)).toEqual(["l1"]);
    expect(ui.app().listingsState.items.map((i) => i.id)).toEqual(["l1"]);
    expect(ui.text()).not.toContain("Buc-ee's Tie-Dye Tee");
    await act(async () => { ui.root.unmount(); });
  });
});

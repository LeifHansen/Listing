/* An ended listing does not sit on the grid waiting to be trashed.
 *
 * What the seller was looking at: an "Ended" card among their live ones, a
 * broken-image tile where the photo should be (eBay stops serving the photos
 * of an item that finished a while ago), a "Relist" link, and a trash button
 * they were expected to press — one per listing that ever ended. Their report
 * was three words: these should be removed automatically.
 *
 * Three things have to be true on this screen, and none was:
 *
 *  1. An ended card is never among the live ones. It belongs in the archive
 *     for as long as it is here at all — and where the whole store is shown
 *     at once, it comes after the listings still running, not between them.
 *  2. Ending a listing of the seller's OWN moves it there rather than
 *     destroying it — their photos and the AI's copy are in it, and the
 *     server keeps it for a month so they can relist.
 *  3. A card the store sync made — a copy of an eBay listing, holding
 *     nothing of theirs — goes on its own, with nothing clicked. The sync
 *     sweeps those records; this pins that the screen actually reloads on the
 *     sweep, because a removal the client never re-reads leaves the same card
 *     sitting there until a refresh.
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
  "/api/health": { anthropic_configured: true, ebay_configured: true,
                   ended_grace_days: 30 },
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

// A listing this app created and published: its photos are local files.
const mine = (id, title) => ({
  id, status: "published", updated_at: "2026-09-01T00:00:00Z",
  listing: { title, price: 34, source: "ebay", ebay_listing_id: `1${id}`,
             images: ["img_000.jpg"] },
});

// A copy of an eBay listing the store sync made. Its photos are eBay's, and
// eBay stops serving them weeks after the item ends — the blank cards.
const mirrored = (item, title, status = "published") => ({
  id: `ebay-${item}`, status, updated_at: "2026-09-01T00:00:00Z",
  listing: { title, price: 34, source: "ebay", ebay_listing_id: item,
             image_urls: ["https://i.ebayimg.com/x.jpg"] },
});

/* The server, holding the seller's rows and applying the same rule it does in
   production: a mirror is removed when it ends, the seller's own is kept as
   `ended` and swept later. */
const isMirror = (row) => String(row.id).startsWith("ebay-")
  && !(row.listing?.images || []).length;

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
      const row = state.listings.find((l) => l.id === id);
      if (isMirror(row)) {
        state.listings = state.listings.filter((l) => l.id !== id);
        return json({ ended: true, removed: true, status: "ended" });
      }
      state.listings = state.listings.map(
        (l) => (l.id === id ? { ...l, status: "ended" } : l));
      return json({ ended: true, removed: false, status: "ended" });
    }
    if (path === "/api/ebay/sync-listings") {
      const before = state.listings.length;
      // The sweep: mirrors that have ended, and (not modelled here) the
      // seller's own past the grace period.
      state.listings = state.listings.filter(
        (l) => !(l.status === "ended" && isMirror(l)));
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
    tab: async (id) => {
      await act(async () => { app.setListingsTab(id); });
    },
    settle: async (ms = 10) => {
      await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
    },
  };
}

// The End control belonging to ONE card: every card has one, and the
// innermost wrapper holding both the title and a ⊘ is the card.
function endButtonFor(host, title) {
  const card = [...host.querySelectorAll("div")].filter(
    (d) => (d.textContent || "").includes(title)
      && d.querySelector("button[aria-label='End listing on eBay']")).pop();
  return card && card.querySelector("button[aria-label='End listing on eBay']");
}

async function confirmWith(label) {
  const dialog = document.querySelector("[role='dialog']");
  const button = [...dialog.querySelectorAll("button")]
    .find((b) => (b.textContent || "").trim() === label);
  expect(button, `no "${label}" button in the dialog`).toBeTruthy();
  await act(async () => { button.click(); });
  return dialog;
}

describe("ending a listing from its card", () => {
  beforeEach(() => { localStorage.clear(); vi.useFakeTimers(); });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("moves the seller's own listing to the archive, and says for how long",
    async () => {
      const ui = await mount([mine("l1", "Levi's 527 Boot Cut"),
                              mine("l2", "Buc-ee's Tie-Dye Tee")]);
      await act(async () => { endButtonFor(ui.host, "Buc-ee's Tie-Dye Tee").click(); });
      // The promise the dialog makes is the number the server sweeps on.
      const dialog = document.querySelector("[role='dialog']");
      expect(dialog.textContent).toContain("30 days");
      await confirmWith("End listing");
      await ui.settle();

      // Off the live grid...
      expect(ui.text()).not.toContain("Buc-ee's Tie-Dye Tee");
      // ...but still theirs, under the archive, where Relist lives.
      await ui.tab("inactive");
      expect(ui.text()).toContain("Buc-ee's Tie-Dye Tee");
      await act(async () => { ui.root.unmount(); });
    });

  it("removes a card the store sync made, because nothing in it is theirs",
    async () => {
      const ui = await mount([mine("l1", "Levi's 527 Boot Cut"),
                              mirrored("998877", "Buc-ee's Tie-Dye Tee")]);
      await act(async () => { endButtonFor(ui.host, "Buc-ee's Tie-Dye Tee").click(); });
      const dialog = document.querySelector("[role='dialog']");
      expect(dialog.textContent).toContain("removed");
      await confirmWith("End & remove");
      await ui.settle();

      expect(ui.app().listingsState.items.map((i) => i.id)).toEqual(["l1"]);
      await ui.tab("inactive");
      expect(ui.text()).not.toContain("Buc-ee's Tie-Dye Tee");
      await act(async () => { ui.root.unmount(); });
    });

  it("never shows an ended card among the live ones", async () => {
    // The report itself. An ended listing lives in the archive while it is
    // here at all: not on Active, and on All — the whole store, whose count
    // has to add up to Active plus Inactive — after the listings still
    // running, never between them. The ended one here was touched LAST, so
    // plain recency would have put it first.
    const ui = await mount([
      { ...mine("l1", "Levi's 527 Boot Cut"), updated_at: "2026-09-01T00:00:00Z" },
      { ...mine("e1", "Ended last week"), status: "ended",
        updated_at: "2026-09-02T00:00:00Z" },
    ]);
    await ui.tab("active");
    expect(ui.text()).toContain("Levi's 527 Boot Cut");
    expect(ui.text()).not.toContain("Ended last week");
    await ui.tab("all");
    const all = ui.text();
    expect(all).toContain("Ended last week");
    expect(all.indexOf("Levi's 527 Boot Cut"))
      .toBeLessThan(all.indexOf("Ended last week"));
    await ui.tab("inactive");
    expect(ui.text()).toContain("Ended last week");
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
    // re-check — the pass that sweeps ended records — and it is the only
    // thing that can take this card off the screen.
    markAutoSynced(7);
    const ui = await mount([mine("l1", "Levi's 527 Boot Cut"),
                            mirrored("998877", "Buc-ee's Tie-Dye Tee", "ended")]);
    await ui.tab("inactive");
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

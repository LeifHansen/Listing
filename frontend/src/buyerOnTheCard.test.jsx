/* A buyer has put money on a live listing — a bid on an auction, or a Best
 * Offer waiting for an answer — and the grid shows it two ways: the card is
 * lit from behind in green, and it goes to the top of the list.
 *
 * Before this a bid reached the grid nowhere at all. The offer had its chip,
 * but the card wearing it sat wherever its last edit put it, which on a
 * store of a few hundred is below the fold. The one card the seller has to
 * look at is now the one they do not have to scroll for.
 *
 * Two rules carried over from the offer badge: only a live listing qualifies
 * (a bid on a sold one is settled history), and a number that is ABSENT is
 * "we could not ask", never "nobody" — a card the app has no answer for
 * neither glows nor moves.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
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

function server(listings, metrics) {
  return (url) => {
    const path = String(url);
    if (path.startsWith("/api/ebay/listing-metrics")) {
      return json({ metrics, traffic_ok: true, needs_reconnect: false });
    }
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true },
                    listings: [...listings] });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({});
  };
}

// Leaving the app and coming back, far enough on the clock that the grid
// asks eBay again (see pendingOfferBadge.test for why it is gated).
async function comeBack({ after = 5 * 60000 } = {}) {
  clock += after;
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
    await new Promise((r) => setTimeout(r, 0));
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

async function mount(listings, metrics, { layout, tab } = {}) {
  vi.stubGlobal("fetch", vi.fn(server(listings, metrics)));
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
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  if (tab) await act(async () => { app.setListingsTab(tab); });
  if (layout) await act(async () => { app.setListingsLayout(layout); });
  return { root, host };
}

const live = (id, updated, extra = {}) => ({
  id, status: "published", updated_at: updated,
  listing: { title: `Item ${id}`, price: 89.99, currency: "USD",
             ebay_listing_id: `1100${id}`, ...extra },
});
const auction = (id, updated) => live(id, updated, {
  price: null, listing_format: "AUCTION", auction_start_price: 0.99 });

// The card button for one listing, found by the title it shows.
const card = (host, id) => [...host.querySelectorAll("button")]
  .find((el) => el.textContent.includes(`Item ${id}`));
const lit = (host, id) => card(host, id).classList.contains("card-buyer-glow");

// The bid chip names itself in words, never in colour alone.
const bidChip = (host) => [...host.querySelectorAll("span")]
  .find((el) => /^\d+ bids?( · \S+)?$/.test(el.textContent.trim()));

// The grid's order, as the seller reads it: the titles top to bottom. The
// fixtures name every listing "Item <letter><digit>", and the card's text
// runs the title straight into the chips after it ("Item a2Bid …"), so the
// id is read by its shape rather than up to the next space.
const order = (host) => [...host.querySelectorAll("button")]
  .map((el) => (el.textContent.match(/Item ([a-z]\d)/) || [])[1])
  .filter(Boolean)
  .filter((id, i, all) => all.indexOf(id) === i);

let clock = 0;
const realNow = Date.now.bind(Date);

describe("a buyer on the card", () => {
  beforeEach(() => {
    localStorage.clear();
    clock = 0;
    vi.spyOn(Date, "now").mockImplementation(() => realNow() + clock);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("lights an auction that has taken a bid, and says how much", async () => {
    const { root, host } = await mount([auction("a1", "2026-09-01T00:00:00Z")], {
      a1: { views: 12, watchers: 3, offers: 0, bids: 3, high_bid: 12.5,
            bid_currency: "USD" } });
    expect(lit(host, "a1")).toBe(true);
    expect(bidChip(host).textContent).toContain("3 bids");
    expect(bidChip(host).textContent).toContain("$12.50");
    expect(bidChip(host).getAttribute("title")).toContain("high bid is $12.50");
    await act(async () => { root.unmount(); });
  });

  it("reads one bid as one bid", async () => {
    const { root, host } = await mount([auction("a1", "2026-09-01T00:00:00Z")], {
      a1: { bids: 1, high_bid: 4.25, bid_currency: "USD" } });
    expect(bidChip(host).textContent).toContain("1 bid ·");
    expect(bidChip(host).textContent).not.toContain("bids");
    await act(async () => { root.unmount(); });
  });

  it("keeps the listing's own currency", async () => {
    const { root, host } = await mount([auction("a1", "2026-09-01T00:00:00Z")], {
      a1: { bids: 2, high_bid: 20, bid_currency: "GBP" } });
    expect(bidChip(host).textContent).toContain("£20.00");
    await act(async () => { root.unmount(); });
  });

  it("lights a listing with an offer waiting the same way", async () => {
    // One glow for both: a bid and an offer are the same fact to the seller
    // scanning the grid — somebody has put money on this one.
    const { root, host } = await mount([live("l1", "2026-09-01T00:00:00Z")], {
      l1: { offers: 1, top_offer: 45, offer_currency: "USD", offer_expires_at: "" } });
    expect(lit(host, "l1")).toBe(true);
    expect(bidChip(host)).toBeUndefined();
    await act(async () => { root.unmount(); });
  });

  it("leaves a card nobody has bid on alone", async () => {
    const { root, host } = await mount([auction("a1", "2026-09-01T00:00:00Z")], {
      a1: { views: 12, watchers: 3, offers: 0, bids: 0 } });
    expect(lit(host, "a1")).toBe(false);
    expect(bidChip(host)).toBeUndefined();
    await act(async () => { root.unmount(); });
  });

  it("leaves a card the app could not ask about alone", async () => {
    // No `bids` key at all: eBay's sweep failed or never ran. Silence, not
    // "no bids" — and certainly not a glow.
    const { root, host } = await mount([auction("a1", "2026-09-01T00:00:00Z")], {
      a1: { views: 12 } });
    expect(lit(host, "a1")).toBe(false);
    expect(bidChip(host)).toBeUndefined();
    await act(async () => { root.unmount(); });
  });

  it("does not light a finished listing, where a bid is settled history", async () => {
    const sold = { id: "s1", status: "sold", updated_at: "2026-09-01T00:00:00Z",
      listing: { title: "Item s1", price: null, sold_price: 31, currency: "USD",
                 listing_format: "AUCTION", ebay_listing_id: "1100s1" } };
    const { root, host } = await mount([sold], {
      s1: { bids: 7, high_bid: 31, bid_currency: "USD" } }, { tab: "inactive" });
    expect(host.textContent).toContain("Item s1");
    expect(lit(host, "s1")).toBe(false);
    expect(bidChip(host)).toBeUndefined();
    await act(async () => { root.unmount(); });
  });

  it("brings the listings a buyer is on to the top, newest first among them", async () => {
    // Four live listings, newest edit first as the grid always ordered them.
    // The bid is on the OLDEST; the offer is on the second oldest.
    const listings = [
      live("l1", "2026-09-04T00:00:00Z"),
      live("l2", "2026-09-03T00:00:00Z"),
      live("l3", "2026-09-02T00:00:00Z"),
      auction("a4", "2026-09-01T00:00:00Z"),
    ];
    const { root, host } = await mount(listings, {
      a4: { bids: 2, high_bid: 6, bid_currency: "USD" },
      l3: { offers: 1, top_offer: 45, offer_currency: "USD", offer_expires_at: "" },
      l1: { bids: 0, offers: 0 },
      l2: { bids: 0, offers: 0 },
    });
    // The two with a buyer on them lead, in their own recency order; the
    // rest keep the order they had.
    expect(order(host)).toEqual(["l3", "a4", "l1", "l2"]);
    await act(async () => { root.unmount(); });
  });

  it("does the same in the compact list layout", async () => {
    const listings = [
      live("l1", "2026-09-04T00:00:00Z"),
      auction("a2", "2026-09-01T00:00:00Z"),
    ];
    const { root, host } = await mount(listings, {
      a2: { bids: 1, high_bid: 6, bid_currency: "USD" } }, { layout: "list" });
    expect(order(host)).toEqual(["a2", "l1"]);
    expect(lit(host, "a2")).toBe(true);
    expect(bidChip(host).textContent).toContain("1 bid");
    await act(async () => { root.unmount(); });
  });

  it("puts the card back once the auction has no bid to show", async () => {
    // A retracted bid, or the read that first said so being wrong: the
    // glow and the place at the top last exactly as long as the fact does.
    const listings = [
      live("l1", "2026-09-04T00:00:00Z"),
      auction("a2", "2026-09-01T00:00:00Z"),
    ];
    const metrics = { a2: { bids: 1, high_bid: 6, bid_currency: "USD" } };
    const { root, host } = await mount(listings, metrics);
    expect(order(host)).toEqual(["a2", "l1"]);

    metrics.a2 = { bids: 0 };
    await comeBack();

    expect(order(host)).toEqual(["l1", "a2"]);
    expect(lit(host, "a2")).toBe(false);
    await act(async () => { root.unmount(); });
  });
});

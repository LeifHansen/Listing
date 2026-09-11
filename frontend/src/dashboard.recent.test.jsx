/* A sale leaves the Sell screen. It has to leave the dashboard too.
 *
 * Sold listings are archived: the Sell screen files them under Inactive and
 * subtracts them from every other tab, because a finished sale is the one
 * thing in that grid the seller cannot act on. The dashboard's "Recent
 * listings" strip never got the message — it sorted the WHOLE store by
 * `updated_at` and took the first four.
 *
 * Which is worse than merely showing them. A sale is the last thing that ever
 * touches a row, so the item that had just disappeared from the Sell screen
 * went straight to the FRONT of the dashboard strip and pushed a live listing
 * out of it, on the panel the seller sees first.
 *
 * The same four slots, filled the same careless way, lose the seller money in
 * a second way — reported from the dashboard, with the auction that had a bid
 * on it sitting in the LAST of the four cards behind three nobody had touched.
 * `updated_at` says what was written last, not what is worth looking at, and
 * the jobs that write are bulk: enriching a batch of listings, or relisting
 * one, re-stamps every row it touches. Four of them are four cards. So the
 * grid's rule — a bid or a Best Offer goes to the front, because it is money
 * waiting on an answer — is this strip's rule too, and it is applied before
 * the four are taken rather than after.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { Dashboard } from "@/views/Dashboard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "lahey@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: false },
  "/api/ebay/status": { connected: false },
  "/api/ebay/policies": { policies: [] },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
};

function json(body, status = 200) {
  return Promise.resolve({
    ok: status < 400,
    status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

/** Every route answers normally; the store is whatever `listings` says.

    `metrics` is eBay's per-listing numbers, and passing any at all connects
    the account — the store only asks for them while one is connected, and
    only shows what it was told. Left out, nothing is connected and every
    card's bid count is unknown, which is the state the first tests here
    were written in. */
function server(listings, metrics) {
  const connected = !!metrics;
  return (url) => {
    const path = String(url);
    if (path.startsWith("/api/ebay/listing-metrics")) {
      return json({ metrics: metrics || {}, traffic_ok: true,
                    needs_reconnect: false });
    }
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true }, listings });
    }
    if (connected && path.startsWith("/api/ebay/status")) {
      return json({ connected: true });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" }, 404);
  };
}

const listing = (id, status, title, updated_at) => ({
  id, status, updated_at, created_at: "2026-02-01T00:00:00Z",
  listing: { title, price: 20 },
});

async function mount(listings, metrics) {
  vi.stubGlobal("fetch", vi.fn(server(listings, metrics)));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <Dashboard />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, text: () => host.textContent || "", cards: () => cards(host) };
}

/* The strip's cards, in the order they are on screen. Read as whole cards
   rather than by digging for the title element, so this says what the seller
   sees and survives the card's insides being rearranged. */
function cards(host) {
  const heading = [...host.querySelectorAll("h2")]
    .find((h) => h.textContent.trim() === "Recent listings");
  const grid = heading?.closest(".mb-4")?.nextElementSibling;
  return [...(grid?.children || [])].map((c) => c.textContent || "");
}

describe("the dashboard's Recent listings strip", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("does not keep a listing that has sold", async () => {
    // The report, in order: the sale is the newest row in the store.
    const { root, text } = await mount([
      listing("s1", "sold", "Duck Head Pins Shadow Box", "2026-03-09T00:00:00Z"),
      listing("l1", "live", "Hand Painted Trinket Dish", "2026-03-08T00:00:00Z"),
      listing("l2", "live", "Flamingo Palm Tree Camp Shirt", "2026-03-07T00:00:00Z"),
    ]);

    expect(text()).not.toContain("Duck Head Pins Shadow Box");
    await act(async () => { root.unmount(); });
  });

  it("gives the slot back to a live listing instead of leaving a gap", async () => {
    // Filtering after the slice would show three cards here, not four: the
    // strip is four wide, and the fifth listing has to move up into the space
    // the sale vacated.
    const { root, text } = await mount([
      listing("s1", "sold", "Sold Shadow Box", "2026-03-09T00:00:00Z"),
      listing("l1", "live", "Trinket Dish", "2026-03-08T00:00:00Z"),
      listing("l2", "live", "Camp Shirt", "2026-03-07T00:00:00Z"),
      listing("l3", "live", "Work Polo Shirt", "2026-03-06T00:00:00Z"),
      listing("l4", "live", "Enamel Coffee Pot", "2026-03-05T00:00:00Z"),
    ]);

    for (const title of ["Trinket Dish", "Camp Shirt", "Work Polo Shirt", "Enamel Coffee Pot"]) {
      expect(text()).toContain(title);
    }
    expect(text()).not.toContain("Sold Shadow Box");
    await act(async () => { root.unmount(); });
  });

  it("does not tell a seller who sold the lot that they have no listings", async () => {
    // The other half of the fix. An empty strip has three causes now, and
    // "you haven't listed anything" is only one of them -- the same rule the
    // failed-read card next door already follows.
    const { root, text } = await mount([
      listing("s1", "sold", "Sold Shadow Box", "2026-03-09T00:00:00Z"),
      listing("s2", "sold", "Sold Trinket Dish", "2026-03-08T00:00:00Z"),
    ]);

    expect(text()).not.toContain("No listings yet");
    expect(text()).toContain("Everything's sold");
    await act(async () => { root.unmount(); });
  });

  it("still says the store is empty when it really is", async () => {
    const { root, text } = await mount([]);

    expect(text()).toContain("No listings yet");
    expect(text()).not.toContain("Everything's sold");
    await act(async () => { root.unmount(); });
  });

  it("keeps a listing with a bid on it when newer ones are enriched", async () => {
    // The report: the auction is the OLDEST row in the store, because the
    // four above it were written after it by an enrich pass and a relist —
    // and it is the only one a buyer has put money on.
    const { root, cards, text } = await mount([
      listing("l1", "live", "Beanie Bear", "2026-03-09T00:00:00Z"),
      listing("l2", "live", "Wooden Rabbit Figurine", "2026-03-08T00:00:00Z"),
      listing("l3", "live", "Spiritomb Holo", "2026-03-07T00:00:00Z"),
      listing("l4", "live", "Enamel Coffee Pot", "2026-03-06T00:00:00Z"),
      listing("l5", "live", "Levi's 501 Black Wash", "2026-03-01T00:00:00Z"),
    ], { l5: { bids: 1, high_bid: 12.44 } });

    expect(cards()[0]).toContain("Levi's 501 Black Wash");
    // Four slots, still four cards: the lift costs the oldest of the
    // enriched ones its place, not the strip a card.
    expect(cards()).toHaveLength(4);
    expect(text()).not.toContain("Enamel Coffee Pot");
    await act(async () => { root.unmount(); });
  });

  it("leaves the order alone when nobody has bid", async () => {
    // The lift is a bid, not eBay being connected: with the numbers read and
    // every count zero, the strip is the recency order it always was.
    const { root, cards } = await mount([
      listing("l1", "live", "Beanie Bear", "2026-03-09T00:00:00Z"),
      listing("l2", "live", "Wooden Rabbit Figurine", "2026-03-08T00:00:00Z"),
      listing("l3", "live", "Levi's 501 Black Wash", "2026-03-01T00:00:00Z"),
    ], { l3: { bids: 0, views: 12 } });

    expect(cards()).toHaveLength(3);
    expect(cards()[0]).toContain("Beanie Bear");
    expect(cards()[2]).toContain("Levi's 501 Black Wash");
    await act(async () => { root.unmount(); });
  });
});

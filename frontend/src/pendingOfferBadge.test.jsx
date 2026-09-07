/* A buyer's offer, waiting on the seller, on the listing's own card.
 *
 * eBay gives a Best Offer 48 hours. Miss it and it lapses — the sale is lost
 * without the seller ever having declined it. The card already carried views
 * and watchers, both of which keep until the next time the grid is opened,
 * and said nothing about the one number attached to a person waiting.
 *
 * The rule the badge is built on: it appears only where eBay actually
 * answered. A count that is ABSENT means the app couldn't ask (see
 * services/metrics) — drawing "no offers" from that would be the app telling
 * a seller nobody is waiting on the strength of having failed to find out.
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
      return json({ authed: true, db: { configured: true, connected: true }, listings });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({});
  };
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

const live = (id, extra = {}) => ({
  id, status: "published", updated_at: "2026-09-01T00:00:00Z",
  listing: { title: `Item ${id}`, price: 89.99, currency: "USD",
             ebay_listing_id: `1100${id}`, ...extra },
});

// The chip names itself in words, never in colour alone: this finds it by the
// text a screen reader would read out. (The leading space is the gap between
// the icon and the label.)
const chip = (host) => [...host.querySelectorAll("span")]
  .find((el) => /^(Offer \S+|\d+ offers( · \S+)?|1 offer)$/
    .test(el.textContent.trim()));

describe("the pending-offer badge", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("puts the money a buyer offered on the card", async () => {
    // One offer: the AMOUNT is the fact worth reading. "1 offer" of what?
    const { root, host } = await mount([live("l1")], {
      l1: { views: 12, watchers: 3, offers: 1, top_offer: 45,
            offer_currency: "USD", offer_expires_at: "" } });
    expect(chip(host).textContent).toContain("Offer $45.00");
    await act(async () => { root.unmount(); });
  });

  it("leads with the count when several buyers are waiting, and the best of them", async () => {
    const { root, host } = await mount([live("l1")], {
      l1: { offers: 3, top_offer: 52.5, offer_currency: "USD",
            offer_expires_at: "" } });
    expect(chip(host).textContent).toContain("3 offers");
    expect(chip(host).textContent).toContain("$52.50");
    await act(async () => { root.unmount(); });
  });

  it("says what to do about it, and when it runs out", async () => {
    // The app reads offers; eBay answers them. A tooltip implying a control
    // here that doesn't exist would send the seller looking for one.
    const soon = new Date(Date.now() + 3 * 3600 * 1000).toISOString();
    const { root, host } = await mount([live("l1")], {
      l1: { offers: 1, top_offer: 45, offer_currency: "USD",
            offer_expires_at: soon } });
    const title = chip(host).getAttribute("title");
    expect(title).toContain("A buyer offered $45.00");
    expect(title).toContain("in eBay");
    expect(title).toContain("expires in 3h");
    await act(async () => { root.unmount(); });
  });

  it("falls back to the general rule rather than counting down from a passed deadline", async () => {
    // eBay only reports an offer as pending, so a deadline already behind us
    // is clock skew, not a lapsed offer. The chip stays — "in -4m" would not.
    const gone = new Date(Date.now() - 4 * 60 * 1000).toISOString();
    const { root, host } = await mount([live("l1")], {
      l1: { offers: 1, top_offer: 45, offer_currency: "USD",
            offer_expires_at: gone } });
    const title = chip(host).getAttribute("title");
    expect(title).toContain("offers expire after 48 hours");
    expect(title).not.toContain("in -");
    await act(async () => { root.unmount(); });
  });

  it("keeps the listing's own currency", async () => {
    // A seller on eBay.co.uk is not offered dollars.
    const { root, host } = await mount([live("l1")], {
      l1: { offers: 1, top_offer: 45, offer_currency: "GBP",
            offer_expires_at: "" } });
    expect(chip(host).textContent).toContain("£45.00");
    await act(async () => { root.unmount(); });
  });

  it("stays off a card nobody has offered on", async () => {
    const { root, host } = await mount([live("l1")], {
      l1: { views: 12, watchers: 3, offers: 0 } });
    expect(chip(host)).toBeUndefined();
    expect(host.textContent).toContain("Item l1");
    await act(async () => { root.unmount(); });
  });

  it("stays off a card the app could not ask about", async () => {
    // The load-bearing case. No `offers` key at all — eBay's lookup failed or
    // never ran. Silence, not "no offers".
    const { root, host } = await mount([live("l1")], {
      l1: { views: 12, watchers: 3 } });
    expect(chip(host)).toBeUndefined();
    await act(async () => { root.unmount(); });
  });

  it("stays off anything that is not live, where an offer is settled history", async () => {
    const sold = { id: "s1", status: "sold", updated_at: "2026-09-01T00:00:00Z",
      listing: { title: "Item s1", price: 89.99, sold_price: 76.5,
                 currency: "USD", ebay_listing_id: "1100s1" } };
    const { root, host } = await mount([sold], {
      s1: { offers: 2, top_offer: 45, offer_currency: "USD" } }, { tab: "inactive" });
    // The sold card renders; the chip is not on it.
    expect(host.textContent).toContain("Item s1");
    expect(chip(host)).toBeUndefined();
    await act(async () => { root.unmount(); });
  });

  it("rides along in the compact list layout too", async () => {
    // Two layouts, one fact. The row's badge line carries it beside the
    // status, ahead of origin and traffic.
    const { root, host } = await mount([live("l1")], {
      l1: { offers: 1, top_offer: 45, offer_currency: "USD",
            offer_expires_at: "" } }, { layout: "list" });
    expect(chip(host).textContent).toContain("Offer $45.00");
    await act(async () => { root.unmount(); });
  });
});

/* The dashboard has to show the seller's money, not dollars.
 *
 * `salesSummary` now reports which currency it counted (see lib/money.test.js).
 * This is the other half: that the screen actually uses it. The bug it closes
 * was reported once already, at the sold notification — a seller on eBay.co.uk
 * told their £45 item sold "for $45.00" — and the fix went in there and not
 * here, where the same four numbers are the first thing they see.
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

function server(listings) {
  return (url) => {
    const path = String(url);
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true }, listings });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" });
  };
}

const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();

function soldListing(id, price, currency) {
  return {
    id, status: "sold",
    listing: { title: `Item ${id}`, sold_price: price, sold_quantity: 1,
               currency, sold_at: yesterday, purchase_price: 5 },
  };
}

function liveListing(id, price, currency) {
  return { id, status: "published", listing: { title: `Live ${id}`, price, currency } };
}

async function mount(listings) {
  vi.stubGlobal("fetch", vi.fn(server(listings)));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><Dashboard /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, text: () => host.textContent || "" };
}

describe("the money on the dashboard", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("is the currency the seller actually sold in", async () => {
    const { root, text } = await mount([soldListing("s1", 45, "GBP")]);
    expect(text()).toContain("£45.00");
    expect(text()).not.toContain("$45.00");
    await act(async () => { root.unmount(); });
  });

  it("prices the live store in its own currency too", async () => {
    const { root, text } = await mount([liveListing("l1", 30, "GBP")]);
    expect(text()).toContain("£30.00 listed");
    await act(async () => { root.unmount(); });
  });

  it("shows a dash rather than a total that spans two currencies", async () => {
    const { root, text } = await mount([
      soldListing("s1", 45, "GBP"), soldListing("s2", 20, "USD"),
    ]);
    expect(text()).not.toContain("$65.00");
    expect(text()).not.toContain("£65.00");
    expect(text()).toContain("more than one currency");
    await act(async () => { root.unmount(); });
  });

  it("still reads as dollars for a seller who sells in dollars", async () => {
    const { root, text } = await mount([soldListing("s1", 45, "USD")]);
    expect(text()).toContain("$45.00");
    await act(async () => { root.unmount(); });
  });

  it("still shows a zero total for a quiet week", async () => {
    // The empty case must not be swept up by the mixed-currency dash: no
    // sales is a real answer, and "$0.00" is the right way to say it.
    const { root, text } = await mount([liveListing("l1", 30, "USD")]);
    expect(text()).toContain("$0.00");
    await act(async () => { root.unmount(); });
  });
});

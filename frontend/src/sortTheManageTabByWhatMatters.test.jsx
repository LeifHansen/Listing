/* The Manage grid in the order the seller asks for.
 *
 * The grid had one order — buyers waiting first, then whatever was touched
 * last — and that is still the default. What this pins is the menu that
 * changes it: that the cards move, that the choice is named on the button,
 * that it survives a reload on this device (a sort hides nothing, so it is
 * safe to remember where a filter is not), and that a saved view brings its
 * own order back.
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
  "/api/health": { anthropic_configured: true, ebay_configured: false },
  "/api/ebay/status": { connected: false },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/listing-views": { views: [] },
};

const json = (body) => Promise.resolve({
  ok: true, status: 200, headers: { get: () => "application/json" },
  json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)),
});

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

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

async function mount(listings) {
  vi.stubGlobal("fetch", vi.fn(server(listings)));
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
  return { root, host, app: () => app };
}

const live = (id, title, price, startedDaysAgo) => ({
  id, status: "published",
  created_at: "2026-11-01T00:00:00Z",
  updated_at: `2026-11-${String(10 + id.charCodeAt(0) % 10).padStart(2, "0")}T00:00:00Z`,
  listing: {
    title, price, images: ["a.jpg"],
    ebay_start_time: new Date(Date.now() - startedDaysAgo * 86400000).toISOString(),
  },
});

const STORE = [
  live("a", "Denim jacket", 45, 30),
  live("b", "Wool scarf", 12, 2),
  live("c", "Leather boots", 90, 120),
];

// The card titles, in the order they are on screen.
const order = (host) => [...host.querySelectorAll("p, span")]
  .map((n) => n.textContent)
  .filter((t) => ["Denim jacket", "Wool scarf", "Leather boots"].includes(t))
  .filter((t, i, all) => all.indexOf(t) === i);

const button = (host, label) => [...host.querySelectorAll("button")]
  .find((b) => (b.getAttribute("aria-label") || b.textContent).startsWith(label));

describe("sorting the Manage tab", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("reorders the cards from the Sort menu, and names the order on the button", async () => {
    const { root, host } = await mount(STORE);

    await act(async () => { button(host, "Sort:").click(); });
    const cheap = [...host.querySelectorAll('[role="menuitemradio"]')]
      .find((b) => b.textContent.includes("Price: low to high"));
    expect(cheap.getAttribute("aria-checked")).toBe("false");
    await act(async () => { cheap.click(); });

    expect(order(host)).toEqual(["Wool scarf", "Denim jacket", "Leather boots"]);
    expect(button(host, "Sort:").getAttribute("aria-label")).toBe("Sort: Price: low to high");
    await act(async () => { root.unmount(); });
  });

  it("sorts by when each listing went up on eBay, and says the date on the card", async () => {
    const { root, host, app } = await mount(STORE);
    await act(async () => { app().setListingsSort("listed-asc"); });
    expect(order(host)).toEqual(["Leather boots", "Denim jacket", "Wool scarf"]);
    expect(host.textContent).toMatch(/Listed [A-Z][a-z]{2} \d/);
    await act(async () => { root.unmount(); });
  });

  it("remembers the order on this device, unlike a filter", async () => {
    const first = await mount(STORE);
    await act(async () => { first.app().setListingsSort("title-asc"); });
    await act(async () => { first.app().setListingFilters({ brand: "x" }); });
    await act(async () => { first.root.unmount(); });
    document.body.innerHTML = "";

    const again = await mount(STORE);
    expect(again.app().listingsSort).toBe("title-asc");
    expect(again.app().listingFilters.brand).toBe("");
    expect(order(again.host)).toEqual(["Denim jacket", "Leather boots", "Wool scarf"]);
    await act(async () => { again.root.unmount(); });
  });

  it("brings a saved view's order back with it", async () => {
    const { root, app } = await mount(STORE);
    await act(async () => {
      app().applyListingView({ id: "v1", name: "Oldest", tab: "active",
        filters: { priceMin: "1" }, sort: "listed-asc" });
    });
    expect(app().listingsSort).toBe("listed-asc");
    await act(async () => { root.unmount(); });
  });

  it("leaves the order alone for a view saved without one", async () => {
    const { root, app } = await mount(STORE);
    await act(async () => { app().setListingsSort("price-desc"); });
    await act(async () => {
      app().applyListingView({ id: "v1", name: "Any", tab: "active", filters: { priceMin: "1" } });
    });
    expect(app().listingsSort).toBe("price-desc");
    await act(async () => { root.unmount(); });
  });
});

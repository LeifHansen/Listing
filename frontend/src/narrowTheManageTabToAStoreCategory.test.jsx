/* One tap to a shelf: the Manage tab's store and eBay category pickers.
 *
 * The filter panel had a "Category contains" text box, which is a search, not
 * a pick: a seller had to remember what they called a shelf and spell it.
 * The pickers list what is actually in the store — the seller's own Store
 * shelves in their own order, and the eBay categories their listings are
 * filed under — each with a count, and ticking one narrows the grid.
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
  "/api/ebay/status": { connected: true, username: "seller1" },
  "/api/ebay/store-categories": {
    store: true, checked: true,
    categories: [
      { id: 11, name: "Vintage Tees", path: "Vintage Tees", level: 1 },
      { id: 12, name: "Beanies", path: "Beanies", level: 1 },
    ],
  },
  "/api/ebay/listing-metrics": { metrics: {} },
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
    return key ? json(BASE[key]) : json({});
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
  for (let i = 0; i < 3; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
  return { root, host, app: () => app };
}

const live = (id, listing) => ({
  id, status: "published",
  created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-01T00:00:00Z",
  listing: { title: `Item ${id}`, price: 10, images: ["a.jpg"], ...listing },
});

const STORE = [
  live("tee1", { store_category_id: "11", store_category_name: "Vintage Tees",
    category_id: "15687", category_suggestion: "Clothing:Men:T-Shirts" }),
  live("tee2", { store_category_id: "11", store_category_name: "Vintage Tees",
    category_id: "15687", category_suggestion: "Clothing:Men:T-Shirts" }),
  live("hat", { store_category_id: "12", store_category_name: "Beanies",
    category_id: "52365", category_suggestion: "Clothing:Men:Hats" }),
  live("loose", { store_category_id: "", category_id: "52365",
    category_suggestion: "Clothing:Men:Hats" }),
];

const button = (host, text) => [...host.querySelectorAll("button")]
  .find((b) => b.textContent.trim().startsWith(text));
const checkbox = (host, name) => [...host.querySelectorAll("label")]
  .find((l) => l.textContent.startsWith(name))?.querySelector("input[type=checkbox]");

describe("the store category picker", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("lists the store's shelves with how much is on each, and narrows to the ticked one", async () => {
    const { root, host } = await mount(STORE);
    await act(async () => { button(host, "Store category").click(); });

    const tees = [...host.querySelectorAll("label")].find((l) => l.textContent.startsWith("Vintage Tees"));
    expect(tees.textContent).toBe("Vintage Tees2");
    expect(host.textContent).toContain("No store category");

    await act(async () => { checkbox(host, "Vintage Tees").click(); });
    expect(host.textContent).toContain("Item tee1");
    expect(host.textContent).toContain("Item tee2");
    expect(host.textContent).not.toContain("Item hat");
    expect(host.textContent).toContain("Store: Vintage Tees");
    expect(host.textContent).toMatch(/Showing 2 of 4/);
    await act(async () => { root.unmount(); });
  });

  it("finds the listings on no shelf", async () => {
    const { root, host } = await mount(STORE);
    await act(async () => { button(host, "Store category").click(); });
    await act(async () => { checkbox(host, "No store category").click(); });
    expect(host.textContent).toContain("Item loose");
    expect(host.textContent).not.toContain("Item tee1");
    await act(async () => { root.unmount(); });
  });
});

describe("the eBay category picker", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("offers the categories the listings are filed under, by their readable name", async () => {
    const { root, host } = await mount(STORE);
    await act(async () => { button(host, "eBay category").click(); });
    expect(checkbox(host, "Hats")).toBeTruthy();
    await act(async () => { checkbox(host, "Hats").click(); });
    expect(host.textContent).toContain("Item hat");
    expect(host.textContent).toContain("Item loose");
    expect(host.textContent).not.toContain("Item tee1");
    expect(host.textContent).toContain("eBay: Hats");
    await act(async () => { root.unmount(); });
  });

  it("keeps counting every choice off the whole tab while one is ticked", async () => {
    const { root, host, app } = await mount(STORE);
    await act(async () => { app().setListingFilters({ ebayCategory: ["52365"] }); });
    await act(async () => { button(host, "eBay category").click(); });
    const shirts = [...host.querySelectorAll("label")].find((l) => l.textContent.startsWith("T-Shirts"));
    expect(shirts.textContent).toBe("T-Shirts2");
    await act(async () => { root.unmount(); });
  });
});

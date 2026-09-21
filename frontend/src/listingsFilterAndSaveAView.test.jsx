/* Filtering the store, and keeping the filter under a name.
 *
 * The listings grid could be cut two ways — the lifecycle tabs and the search
 * box — and neither answers the questions a seller with a few hundred items
 * has: "everything Nike under $20", "the drafts still missing photos". Worse,
 * a seller who worked one out by scrolling had to work it out again tomorrow.
 *
 * Two things have to hold for a filter bar to be safe on a screen that is
 * also how somebody checks their store is still there.
 *
 * A cut list must SAY it was cut. Fewer cards is also what an outage, a
 * half-finished sync and a deleted batch look like, so the count and the
 * chips are not decoration: without them a seller who forgot a filter was on
 * is looking at an empty store with a button to create their first listing.
 * The tab badges keep counting the whole tab for the same reason — move them
 * and no number anywhere says how big the store really is.
 *
 * And a saved view must survive the browser it was made in. It rides the
 * account, so what this checks is that saving writes the whole strip to the
 * server and that applying one restores BOTH halves — the tab it was saved on
 * and the filters on top of it. A view that restored only the filters is a
 * different list under the same name.
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

/* A server that keeps the seller's saved views, so a PUT and the next GET
   agree — the strip on screen is only ever the server's answer. */
function server(listings, { views = [], signedIn = true } = {}) {
  const state = { views: [...views], puts: [] };
  const fetcher = (url, opts = {}) => {
    const path = String(url);
    if (path.startsWith("/api/listing-views")) {
      if ((opts.method || "GET") === "PUT") {
        const sent = JSON.parse(opts.body).views;
        state.puts.push(sent);
        state.views = sent;
        return json({ ok: true, views: state.views });
      }
      return json({ views: state.views });
    }
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true }, listings });
    }
    if (path.startsWith("/api/auth/me") && !signedIn) return json({ user: null });
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" });
  };
  fetcher.state = state;
  return fetcher;
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

async function mount(listings, opts) {
  const fetcher = server(listings, opts);
  vi.stubGlobal("fetch", vi.fn(fetcher));
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
  return { root, host, app: () => app, state: fetcher.state };
}

const live = (id, listing) => ({
  id, status: "published",
  created_at: "2026-09-18T00:00:00Z", updated_at: "2026-09-18T00:00:00Z",
  listing: { title: `Item ${id}`, images: ["a.jpg"], ...listing },
});

const STORE = [
  live("a", { brand: "Nike", price: 18 }),
  live("b", { brand: "Nike", price: 90 }),
  live("c", { brand: "Adidas", price: 12 }),
];

const cardTitles = (host) =>
  [...host.querySelectorAll("*")].length ? host.textContent : "";

// The tab strip's badge for one tab, as rendered.
const tabCount = (host, label) => {
  const btn = [...host.querySelectorAll("button")]
    .find((b) => b.textContent.startsWith(label));
  return btn ? btn.textContent.slice(label.length).trim() : null;
};

describe("filtering the listings grid", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("cuts the grid down to what the filters ask for", async () => {
    const { root, host, app } = await mount(STORE);
    expect(cardTitles(host)).toContain("Item c");

    await act(async () => { app().setListingFilters({ brand: "nike" }); });
    expect(cardTitles(host)).toContain("Item a");
    expect(cardTitles(host)).not.toContain("Item c");

    await act(async () => { root.unmount(); });
  });

  it("says how much it cut, rather than leaving it to be noticed", async () => {
    // A filtered grid and a store that lost something look identical.
    const { root, host, app } = await mount(STORE);
    await act(async () => {
      app().setListingFilters({ brand: "nike", priceMax: "20" });
    });
    expect(host.textContent).toMatch(/Showing 1 of 3/);
    await act(async () => { root.unmount(); });
  });

  it("names each filter on a chip that clears it", async () => {
    const { root, host, app } = await mount(STORE);
    await act(async () => { app().setListingFilters({ brand: "Nike" }); });

    const chip = [...host.querySelectorAll("button")]
      .find((b) => (b.getAttribute("aria-label") || "").startsWith("Clear filter:"));
    expect(chip).toBeTruthy();
    expect(host.textContent).toContain("Brand: Nike");

    await act(async () => { chip.click(); });
    expect(host.textContent).toContain("Item c");   // the whole tab is back
    await act(async () => { root.unmount(); });
  });

  it("leaves the tab badge counting the whole tab", async () => {
    // The badge is a claim about the tab. Moving it with the filter would
    // leave no number anywhere saying how big the store is.
    const { root, host, app } = await mount(STORE);
    expect(tabCount(host, "Active")).toBe("3");
    await act(async () => { app().setListingFilters({ brand: "nike" }); });
    expect(tabCount(host, "Active")).toBe("3");
    expect(host.textContent).toMatch(/Showing 2 of 3/);
    await act(async () => { root.unmount(); });
  });

  it("does not report a filtered-out tab as an empty store", async () => {
    // The finding this is here to prevent: "No listings yet", with a button
    // to create your first one, shown to a seller with three.
    const { root, host, app } = await mount(STORE);
    await act(async () => { app().setListingFilters({ brand: "reebok" }); });
    expect(host.textContent).toContain("No matches");
    expect(host.textContent).not.toContain("Nothing live yet");
    expect(host.textContent).toMatch(/match the filters/i);
    await act(async () => { root.unmount(); });
  });

  it("offers the way back out of an empty filtered tab", async () => {
    const { root, host, app } = await mount(STORE);
    await act(async () => { app().setListingFilters({ brand: "reebok" }); });
    const back = [...host.querySelectorAll("button")]
      .find((b) => b.textContent.trim() === "Clear filters");
    expect(back).toBeTruthy();
    await act(async () => { back.click(); });
    expect(host.textContent).toContain("Item a");
    await act(async () => { root.unmount(); });
  });

  it("does not offer to narrow a store with nothing in it", async () => {
    const { root, host } = await mount([]);
    expect(host.textContent).not.toContain("Filters");
    expect(host.textContent).toContain("Nothing live yet");
    await act(async () => { root.unmount(); });
  });

  it("still offers them to an empty store that has a saved view", async () => {
    // The seller has a view; the controls are how they reach it.
    const saved = [{ id: "v1", name: "Mine", tab: "all", filters: { brand: "Nike" } }];
    const { root, host } = await mount([], { views: saved });
    expect(host.textContent).toContain("Mine");
    await act(async () => { root.unmount(); });
  });

  it("shows no filter bar to a visitor who is not signed in", async () => {
    const { root, host } = await mount([], { signedIn: false });
    expect(host.textContent).not.toContain("Filters");
    expect(host.textContent).toContain("Log in to see your listings");
    await act(async () => { root.unmount(); });
  });
});

describe("saving a filter as a view", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("writes it to the account, not to this browser", async () => {
    const { root, host, app, state } = await mount(STORE);
    await act(async () => { app().setListingFilters({ brand: "Nike" }); });
    await act(async () => { await app().saveListingView("Nike stock"); });

    expect(state.puts).toHaveLength(1);
    expect(state.puts[0][0].name).toBe("Nike stock");
    expect(state.puts[0][0].filters.brand).toBe("Nike");
    expect(state.puts[0][0].tab).toBe("active");
    expect(host.textContent).toContain("Nike stock");
    await act(async () => { root.unmount(); });
  });

  it("stores the question, never the listings it matched", async () => {
    // A view that stored ids would go stale the first time it was used.
    const { root, app, state } = await mount(STORE);
    await act(async () => { app().setListingFilters({ brand: "Nike" }); });
    await act(async () => { await app().saveListingView("Nike stock"); });

    expect(JSON.stringify(state.puts[0])).not.toContain("Item a");
    await act(async () => { root.unmount(); });
  });

  it("brings back both halves — the tab and the filters", async () => {
    const saved = [{
      id: "v1", name: "Big sales", tab: "inactive",
      filters: { priceMin: "50" }, created_at: "2026-09-01T00:00:00Z",
    }];
    const { root, app } = await mount(STORE, { views: saved });
    await act(async () => { app().applyListingView(saved[0]); });

    expect(app().listingsTab).toBe("inactive");
    expect(app().listingFilters.priceMin).toBe("50");
    await act(async () => { root.unmount(); });
  });

  it("marks the saved view the screen is actually showing", async () => {
    const saved = [{
      id: "v1", name: "Nike stock", tab: "active",
      filters: { brand: "Nike" }, created_at: "2026-09-01T00:00:00Z",
    }];
    const { root, host, app } = await mount(STORE, { views: saved });
    const pill = () => [...host.querySelectorAll("button")]
      .find((b) => b.getAttribute("title") === "Show “Nike stock”");

    expect(pill().getAttribute("aria-pressed")).toBe("false");
    await act(async () => { app().applyListingView(saved[0]); });
    expect(pill().getAttribute("aria-pressed")).toBe("true");

    // ...and lets go the moment the filters move off it.
    await act(async () => { app().setListingFilters({ brand: "Nike", priceMax: "5" }); });
    expect(pill().getAttribute("aria-pressed")).toBe("false");
    await act(async () => { root.unmount(); });
  });

  it("deletes one by writing the strip without it", async () => {
    const saved = [
      { id: "v1", name: "One", tab: "active", filters: { brand: "Nike" } },
      { id: "v2", name: "Two", tab: "all", filters: { brand: "Adidas" } },
    ];
    const { root, host, app, state } = await mount(STORE, { views: saved });
    await act(async () => { await app().deleteListingView("v1"); });

    expect(state.puts[0].map((v) => v.id)).toEqual(["v2"]);
    expect(host.textContent).not.toContain("One");
    expect(host.textContent).toContain("Two");
    await act(async () => { root.unmount(); });
  });

  it("does not offer to save a view of nothing", async () => {
    // A pill that does nothing when pressed.
    const { root, host, app } = await mount(STORE);
    expect(host.textContent).not.toContain("Save view");
    await act(async () => { app().setListingFilters({ brand: "Nike" }); });
    expect(host.textContent).toContain("Save view");
    await act(async () => { root.unmount(); });
  });

  it("keeps the strip out of the next account's screen", async () => {
    const saved = [{ id: "v1", name: "Mine", tab: "active", filters: { brand: "Nike" } }];
    const { root, host, app } = await mount(STORE, { views: saved });
    expect(host.textContent).toContain("Mine");

    await act(async () => { app().clearSignedInState(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(host.textContent).not.toContain("Mine");
    await act(async () => { root.unmount(); });
  });
});

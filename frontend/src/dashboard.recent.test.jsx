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

function json(body, status = 200) {
  return Promise.resolve({
    ok: status < 400,
    status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

/** Every route answers normally; the store is whatever `listings` says. */
function server(listings) {
  return (url) => {
    const path = String(url);
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true }, listings });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" }, 404);
  };
}

const listing = (id, status, title, updated_at) => ({
  id, status, updated_at, created_at: "2026-02-01T00:00:00Z",
  listing: { title, price: 20 },
});

async function mount(listings) {
  vi.stubGlobal("fetch", vi.fn(server(listings)));
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
  return { root, text: () => host.textContent || "" };
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
});

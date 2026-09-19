/* A seller on two marketplaces reads the listings pipeline a second way.
 *
 * Once Etsy is connected the manager grows a second cut — where a listing
 * lives — with "eBay only, not on Etsy" as the crosspost's shopping list; a
 * tick box stands on every live card; and the first tick brings a bar with
 * "Crosspost to Etsy". An eBay-only seller sees none of this: no pills, no
 * ticks, the pipeline exactly as it was.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { ListingsView } from "@/views/ListingsView";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const ETSY = { key: "etsy", label: "Etsy", connected: true, oauth_ready: true,
  username: "MugsByLeif", supports: {} };
const EBAY = { key: "ebay", label: "eBay", connected: true, oauth_ready: true, supports: {} };

const BASE = (roster) => ({
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: true, username: "leif" },
  "/api/ebay/policies": { policies: [] },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: roster },
  "/api/etsy/settings-options": { shipping_profiles: [], return_policies: [],
    readiness_states: [], selected: { shipping_profile_id: "1", return_policy_id: "2",
      readiness_state_id: "3" } },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
});

function json(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function server(listings, roster) {
  const base = BASE(roster);
  return (url) => {
    const path = String(url);
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true }, listings });
    }
    const key = Object.keys(base).find((k) => path.startsWith(k));
    return key ? json(base[key]) : json({ detail: "Not found" });
  };
}

const live = (id, marketplaces) => ({
  id, status: "published", updated_at: "2026-09-01T00:00:00Z",
  listing: { title: `Item ${id}`, description: "Nice.", price: 24.99, quantity: 1,
    images: [`${id}.jpg`], ebay_listing_id: `1${id}`, marketplaces,
    etsy: { taxonomy_id: 1, who_made: "someone_else", when_made: "1990s" } },
});

let host;
let root;

async function mount(listings, roster) {
  vi.stubGlobal("fetch", vi.fn(server(listings, roster)));
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <ListingsView />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const pills = () => host.querySelector("[aria-label='Where a listing lives']");
const ticks = () => [...host.querySelectorAll("input[type=checkbox][aria-label]")];
const text = () => host.textContent;

describe("the listings manager on two marketplaces", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => {
    await act(async () => { root.unmount(); });
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("stays exactly as it was for an eBay-only seller", async () => {
    await mount([live("a", { ebay: { status: "published" } })], [EBAY]);
    expect(pills()).toBeNull();
    expect(text()).not.toContain("Crosspost to Etsy");
    expect(host.querySelectorAll("input[type=checkbox]").length).toBe(0);
  });

  it("grows the where-it-lives pills, and 'eBay only' narrows the grid", async () => {
    await mount([
      live("a", { ebay: { status: "published" }, etsy: { status: "published", listing_id: "9" } }),
      live("b", { ebay: { status: "published" } }),
    ], [EBAY, ETSY]);
    expect(pills()).not.toBeNull();
    expect(text()).toContain("Item a");
    expect(text()).toContain("Item b");
    const only = [...pills().querySelectorAll("button")]
      .find((b) => b.textContent.startsWith("eBay only"));
    await act(async () => { only.click(); });
    expect(text()).not.toContain("Item a");
    expect(text()).toContain("Item b");
    expect(only.getAttribute("aria-pressed")).toBe("true");
  });

  it("ticks a live listing, brings the crosspost bar, and opens the wizard on it", async () => {
    await mount([
      live("a", { ebay: { status: "published" } }),
      live("b", { ebay: { status: "published" }, etsy: { status: "published", listing_id: "9" } }),
    ], [EBAY, ETSY]);
    expect(text()).not.toContain("Crosspost to Etsy");
    const [first] = ticks();
    expect(first).toBeTruthy();
    await act(async () => { first.click(); });
    expect(text()).toContain("Crosspost to Etsy (1)");
    const open = [...host.querySelectorAll("button")]
      .find((b) => b.textContent.includes("Crosspost to Etsy"));
    await act(async () => { open.click(); });
    const dialog = document.querySelector("[role=dialog]") || document.body;
    expect(dialog.textContent).toContain("Item a");
    // Nothing Etsy would refuse on this one, judged against the shop defaults.
    expect(dialog.querySelector("[data-tally]").textContent).toMatch(/1 ready/);
  });

  it("shows the eBay chip beside Etsy's once there is a second marketplace", async () => {
    await mount([
      live("a", { ebay: { status: "published" }, etsy: { status: "published", listing_id: "9" } }),
    ], [EBAY, ETSY]);
    const chips = [...host.querySelectorAll("span[title]")]
      .map((s) => s.getAttribute("title"));
    expect(chips.some((t) => t.startsWith("eBay: published"))).toBe(true);
    expect(chips.some((t) => t.startsWith("Etsy: published"))).toBe(true);
  });
});

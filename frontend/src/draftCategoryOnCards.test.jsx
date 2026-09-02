/* The category belongs on the face of a draft card, everywhere one appears.
 *
 * It decides two things a seller cannot see from a title and a price: where
 * the listing is filed, and — since eBay publishes a different condition
 * ladder per category — which conditions it will even accept. It was on the
 * drafts strip and the bulk queue only, so a draft reached from the dashboard
 * or the listings manager gave no sign of it until the full editor was open.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { Dashboard } from "@/views/Dashboard";
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

async function mount(Screen, listings) {
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
          <Screen />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, host, app: () => app };
}

const draft = (id, category) => ({
  id, status: "draft", updated_at: "2026-09-01T00:00:00Z",
  listing: { title: `Item ${id}`, price: 24.99, category_suggestion: category },
});

// The control's own label — it announces itself as the category, and as
// something you can change.
const categoryControls = (host) =>
  [...host.querySelectorAll("[aria-label^='Category:']")];

describe("the category on a draft card", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("shows on the dashboard's recent cards, ready to change", async () => {
    const { root, host } = await mount(Dashboard,
      [draft("d1", "Collectibles > Animals > Fish")]);
    const controls = categoryControls(host);
    expect(controls.length).toBeGreaterThan(0);
    expect(host.textContent).toContain("Fish");
    await act(async () => { root.unmount(); });
  });

  it("says so when the AI matched no category at all", async () => {
    // The case that actually blocks a publish — and the one a card showing
    // only a title and a price gave no hint of.
    const { root, host } = await mount(Dashboard, [draft("d1", "")]);
    expect(host.textContent).toContain("No category");
    await act(async () => { root.unmount(); });
  });

  it("shows on the listings manager's cards too", async () => {
    // Drafts appear there under "All" — the manager's Active tab is live
    // listings only.
    const { root, host, app } = await mount(ListingsView,
      [draft("d1", "Home & Garden > Kitchen Tools & Gadgets")]);
    await act(async () => { app().setListingsTab("all"); });
    expect(categoryControls(host).length).toBeGreaterThan(0);
    expect(host.textContent).toContain("Kitchen Tools & Gadgets");
    await act(async () => { root.unmount(); });
  });

  it("stays off a live listing, which is not a draft to fix", async () => {
    const live = { id: "l1", status: "published", updated_at: "2026-09-01T00:00:00Z",
      listing: { title: "Live one", price: 30, category_suggestion: "Fish" } };
    const { root, host, app } = await mount(ListingsView, [live]);
    await act(async () => { app().setListingsTab("all"); });
    expect(host.textContent).toContain("Live one");
    expect(categoryControls(host)).toEqual([]);
    await act(async () => { root.unmount(); });
  });
});

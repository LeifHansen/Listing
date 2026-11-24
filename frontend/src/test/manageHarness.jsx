/* A Manage tab on a fake server, for the selection bar's screen tests.
 *
 * Every bulk action test needs the same things: a signed-in seller with eBay
 * connected, some live listings, a job route that records what it was asked
 * and a status route that answers with the run's report. `routes` adds or
 * overrides paths: each is (body, state) -> response body. */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { ListingsView } from "@/views/ListingsView";
import { ManageView } from "@/views/ManageView";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

export const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true, ended_grace_days: 30 },
  "/api/ebay/status": { connected: true, username: "leif" },
  "/api/ebay/policies": { policies: [] },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [
    { key: "ebay", label: "eBay", connected: true, oauth_ready: true, supports: {} },
  ] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
  "/api/listing-views": { views: [] },
  "/api/ebay/listing-metrics": { metrics: {} },
};

export const json = (body, status = 200) => Promise.resolve({
  ok: status < 400, status, headers: { get: () => "application/json" },
  json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)),
});

export const live = (id, title, listing = {}) => ({
  id, status: "published", updated_at: "2026-09-01T00:00:00Z",
  created_at: "2026-09-01T00:00:00Z",
  listing: { title, description: "Nice.", price: 40, quantity: 1,
    images: [`${id}.jpg`], ebay_listing_id: `1${id}`, source: "ebay", ...listing },
});

export async function mountManage(listings, routes = {}, { base = {}, whole = false } = {}) {
  const state = { listings, asked: {}, loads: 0 };
  const all = { ...BASE, ...base };
  vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
    const path = String(url);
    const body = opts.body ? JSON.parse(opts.body) : null;
    const own = Object.keys(routes).find((k) => path === k || path.startsWith(`${k}?`));
    if (own) {
      state.asked[own] = body;
      const out = routes[own](body, state);
      return out && typeof out.then === "function" ? out : json(out);
    }
    if (path.startsWith("/api/listings")) {
      state.loads += 1;
      return json({ authed: true, db: { configured: true, connected: true },
        listings: state.listings });
    }
    const key = Object.keys(all).find((k) => path.startsWith(k));
    return key ? json(all[key]) : json({ detail: "Not found" });
  }));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider>{whole ? <ManageView /> : <ListingsView />}</AppProvider></ToastProvider>);
  });
  for (let i = 0; i < 3; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
  const q = {
    host, root, state,
    text: () => document.body.textContent,
    ticks: () => [...host.querySelectorAll("input[type=checkbox][aria-label]")],
    button: (label) => [...document.body.querySelectorAll("button")]
      .find((b) => (b.textContent || "").trim().includes(label)),
    menuItem: (label) => [...document.body.querySelectorAll('[role="menuitem"]')]
      .find((b) => (b.textContent || "").includes(label)),
    settle: async (ms = 5) => {
      await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
    },
    click: async (el) => { await act(async () => { el.click(); }); },
  };
  q.openMore = async () => {
    await q.click(document.body.querySelector('button[aria-label="More bulk actions"]'));
  };
  q.unmount = async () => {
    await act(async () => { root.unmount(); });
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  };
  return q;
}

/* A job route and its status: the job "runs" by calling `apply(body, state)`
   and finishes with `result`. */
export function job(id, result, apply = () => {}) {
  return {
    start: (body, state) => {
      apply(body, state);
      return { job_id: id, running: true, total: body.listing_ids.length };
    },
    status: (body, state) => ({
      id, done: true, phase: "done",
      current: 1, total_items: 1,
      result: typeof result === "function" ? result(state) : result,
    }),
  };
}

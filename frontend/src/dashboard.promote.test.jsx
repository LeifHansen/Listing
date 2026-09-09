/* "Promote all" promotes the group it is shown on, and says so.
 *
 * The suggestions list is capped, so "Promote listings · 50" can sit over a
 * store with far more unpromoted listings. The button confirmed the group's
 * count and then posted an empty body, which the server read as "the whole
 * store": a dialog that named 50 was a promise about 900, on the one action
 * in the app that spends a percentage of every sale it touches.
 *
 * It sends the group's own listings now, like the other bulk verbs, tells the
 * seller when one run will not cover the group, and reports what the server
 * found rather than a bare success — including the listings eBay says were
 * already promoted, which is the case a seller who promotes in Seller Hub
 * actually sees.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { Dashboard } from "@/views/Dashboard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const RECS = [
  { listing_id: "a", listing_title: "Nike hoodie", type: "promote",
    label: "Promote", reason: "Not promoted yet — promoted listings show up far more often.",
    action: "open", priority: 70, rate: 6.5 },
  { listing_id: "b", listing_title: "Canon AE-1", type: "promote",
    label: "Promote", reason: "Not promoted yet — promoted listings show up far more often.",
    action: "open", priority: 70, rate: 4.0 },
];

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: true },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
};

function json(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function server(calls, { result, bulkCaps } = {}) {
  return (url, opts = {}) => {
    const path = String(url);
    if (path === "/api/ebay/promote-all") {
      calls.push({ path, body: JSON.parse(opts.body || "{}") });
      return json(result || { promoted: 2, total: 2, already_promoted: 0,
                              failed: 0, skipped: 0, deferred: 0,
                              needs_reconnect: false });
    }
    if (path.startsWith("/api/insights")) {
      return json({ recommendations: RECS, bulk_caps: bulkCaps || {} });
    }
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true },
                    listings: [] });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" });
  };
}

async function mount(calls = [], opts) {
  vi.stubGlobal("fetch", vi.fn(server(calls, opts)));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><Dashboard /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  // document.body: the confirm dialog and the toasts are portals.
  return { root, text: () => document.body.textContent || "" };
}

function byText(label) {
  return [...document.body.querySelectorAll("button")]
    .find((b) => (b.textContent || "").trim() === label);
}

async function click(el) {
  expect(el).toBeTruthy();
  await act(async () => { el.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

describe("promoting a whole group at once", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("sends the group's listings — and only those — once confirmed", async () => {
    const calls = [];
    const { root, text } = await mount(calls);

    await click(byText("Promote all"));
    expect(text()).toContain("Promote 2 listings?");
    expect(calls).toHaveLength(0);

    await click(byText("Promote them"));
    expect(calls).toEqual([{ path: "/api/ebay/promote-all",
                             body: { listing_ids: ["a", "b"] } }]);
    expect(text()).toContain("Promoting 2 listings at eBay's recommended rate");
    await act(async () => { root.unmount(); });
  });

  it("does nothing at all if the seller backs out", async () => {
    const calls = [];
    const { root } = await mount(calls);
    await click(byText("Promote all"));
    await click(byText("Cancel"));
    expect(calls).toHaveLength(0);
    await act(async () => { root.unmount(); });
  });

  it("says when one run will not cover the group", async () => {
    // The server caps a run; the dialog has to promise that much, not the
    // badge.
    const { root, text } = await mount([], { bulkCaps: { promote: 1 } });
    await click(byText("Promote all"));
    expect(text()).toContain("Promote 1 of 2 listings?");
    expect(text()).toContain("One run covers 1 of them — the other 1 stay on the list");
    await act(async () => { root.unmount(); });
  });

  it("reports the listings eBay says were already promoted", async () => {
    // The group was computed a while ago; the server re-reads eBay's own ad
    // list before spending, and what it found is the answer, not "done".
    const { root, text } = await mount([], {
      result: { promoted: 0, total: 0, already_promoted: 2, failed: 0,
                skipped: 0, deferred: 0, needs_reconnect: false },
    });
    await click(byText("Promote all"));
    await click(byText("Promote them"));
    expect(text()).toContain("2 already promoted on eBay");
    expect(text()).not.toContain("No live listings to promote");
    await act(async () => { root.unmount(); });
  });
});

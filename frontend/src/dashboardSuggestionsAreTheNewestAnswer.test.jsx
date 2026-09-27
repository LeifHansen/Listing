/* The dashboard's suggestions are the newest answer, not the slowest.
 *
 * Suggestions are re-read on every change to the store's shape, and a batch or
 * a publish run changes it several times in a few seconds. An earlier, slower
 * /api/insights answer that arrived last put back suggestions built for the
 * store as it was — a suggestion the newer answer had already dropped, back
 * on screen with its button live.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { Dashboard } from "@/views/Dashboard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: false },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
};

const STALE = { recommendations: [
  { listing_id: "b", listing_title: "Canon AE-1", type: "lower_price",
    label: "Lower the price", reason: "Live 30 days.", action: "open",
    priority: 68, rate: null }] };

function json(body) {
  return {
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

let root;
let host;

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("the dashboard's suggestions", () => {
  it("are the newest answer, whichever arrives last", async () => {
    let calls = 0;
    let releaseFirst;
    const first = new Promise((r) => { releaseFirst = r; });
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const path = String(url);
      if (path.startsWith("/api/insights")) {
        calls += 1;
        if (calls === 1) {
          await first;            // the slow, early answer
          return json(STALE);
        }
        return json({ recommendations: [] });
      }
      if (path.startsWith("/api/listings")) {
        return json({ authed: true, db: { configured: true, connected: true },
                      listings: [
                        { id: "a", status: "published",
                          updated_at: "2026-09-01T00:00:00Z",
                          listing: { title: "Nike hoodie", price: 40 } },
                        { id: "b", status: "published",
                          updated_at: "2026-09-01T00:00:00Z",
                          listing: { title: "Canon AE-1", price: 90 } }] });
      }
      const key = Object.keys(BASE).find((k) => path.startsWith(k));
      return json(key ? BASE[key] : {});
    }));
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    let app = null;
    await act(async () => {
      root.render(<ToastProvider><AppProvider>
        <Dashboard /><Probe onValue={(a) => { app = a; }} />
      </AppProvider></ToastProvider>);
    });
    for (let i = 0; i < 3; i++) {
      await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    }
    // One listing sells: the store's shape changes, the suggestions are asked
    // for again, and that newer answer -- nothing to suggest for the other,
    // still-live listing -- comes back first.
    await act(async () => { app.patchListing("a", { status: "sold" }); });
    for (let i = 0; i < 3; i++) {
      await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    }
    expect(calls).toBeGreaterThanOrEqual(2);
    releaseFirst();
    for (let i = 0; i < 3; i++) {
      await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    }
    expect(document.body.textContent).not.toContain("Lower prices");
  });
});

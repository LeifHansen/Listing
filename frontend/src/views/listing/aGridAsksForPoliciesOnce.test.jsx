/* A grid of drafts asks eBay for the shipping policies once, not once a card.
 *
 * Every draft card carries a shipping dropdown, and each dropdown asked for
 * /api/ebay/policies on its own while nothing had answered yet: thirty drafts,
 * thirty identical requests on the grid's first paint, each a live round of
 * eBay account calls against the allowance every seller shares.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { DraftsStrip } from "@/views/listing/DraftsStrip";
import { etsyBlockers } from "@/views/listing/blockers";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const POLICIES = { policies: { fulfillment: [
  { id: "F1", name: "Ground", summary: "USPS Ground" },
  { id: "F2", name: "Priority", summary: "USPS Priority" }] },
  selected: { fulfillment_policy_id: "F1" } };

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: true, ebay_username: "seller" },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
};

function json(body) {
  return {
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}

const draft = (id) => ({
  id, status: "draft", updated_at: "2026-03-09T00:00:00Z",
  listing: { title: `Draft ${id}`, price: 24.99, quantity: 1,
             condition: "USED_EXCELLENT", category_id: "11450",
             images: [`${id}.jpg`], package_weight_lb: 1 },
});

let root;
let host;

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("the shipping policies", () => {
  it("are asked for once for a whole grid of drafts", async () => {
    let asked = 0;
    let release;
    const answer = new Promise((r) => { release = r; });
    const listings = ["a", "b", "c", "d", "e"].map(draft);
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const path = String(url);
      if (path.startsWith("/api/ebay/policies")) {
        asked += 1;
        await answer;               // nothing answers until every card is up
        return json(POLICIES);
      }
      if (path.startsWith("/api/listings")) {
        return json({ authed: true, db: { configured: true, connected: true },
                      listings });
      }
      const key = Object.keys(BASE).find((k) => path.startsWith(k));
      return json(key ? BASE[key] : {});
    }));
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => {
      root.render(<ToastProvider><AppProvider><DraftsStrip /></AppProvider></ToastProvider>);
    });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    release();
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(asked).toBe(1);
  });
});

describe("Etsy's minimum price", () => {
  it("is said in dollars", () => {
    const issues = etsyBlockers({ price: 0.05, title: "x", description: "y",
                                  images: ["a.jpg"] });
    const price = issues.find((i) => i.key === "price" || i.target === "price");
    expect(price, JSON.stringify(issues)).toBeTruthy();
    expect(JSON.stringify(price)).toContain("$0.20");
  });
});

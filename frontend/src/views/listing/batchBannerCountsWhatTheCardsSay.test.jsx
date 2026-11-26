/* The batch's "N of M can't go yet" banner counts what the cards below it
 * say.
 *
 * Each card judges itself with blockersFor -- eBay's rules, and Etsy's when
 * Etsy is among the publish targets -- and Publish all skips exactly those.
 * The banner above them asked eBay's rules alone, so with Etsy picked it
 * reported fewer blocked drafts than the grid was showing, under a comment
 * promising the two could not disagree.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { BulkQueue } from "./BulkMode";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// Publishing to eBay and Etsy, as the chips would say once Etsy is ticked.
vi.mock("./publishShared", async (importOriginal) => ({
  ...(await importOriginal()),
  usePublishTargets: () => ({
    selected: ["ebay", "etsy"], toggle: () => {},
    otherConnected: [{ key: "etsy", label: "Etsy" }],
    effectiveTargets: ["ebay", "etsy"],
  }),
}));

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: true },
  "/api/ebay/policies": { policies: [] },
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

// Everything eBay asks for, and nothing of Etsy's own.
const draft = (id, title) => ({
  id, status: "draft", updated_at: "2026-03-09T00:00:00Z",
  listing: {
    title, price: 24.99, quantity: 1, condition: "USED_EXCELLENT",
    category_id: "11450", images: ["img_000.jpg"],
    package_weight_lb: 1, package_weight_oz: 0,
  },
});

const LISTINGS = [draft("s1", "Canon AE-1"), draft("s2", "Pyrex bowl")];
const DONE = {
  id: "job1", done: true, phase: "done", current: 2, total_items: 2, total_photos: 4,
  items: LISTINGS.map((l) => ({ session_id: l.id, status: "draft", title: l.listing.title })),
};

let root;
let host;

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn((url) => {
    const path = String(url);
    if (path.startsWith("/api/bulk/status/")) return json(DONE);
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true }, listings: LISTINGS });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" });
  }));
});

afterEach(async () => {
  await act(async () => { root.unmount(); });
  host.remove();
  vi.unstubAllGlobals();
});

describe("the batch banner", () => {
  it("counts a draft Etsy would refuse when Etsy is a target", async () => {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => {
      root.render(
        <ToastProvider><AppProvider>
          <BulkQueue jobId="job1" onExit={() => {}} onSettled={() => {}} />
        </AppProvider></ToastProvider>,
      );
    });
    for (let i = 0; i < 3; i += 1) {
      await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    }
    const text = document.body.textContent || "";
    // The cards say so (Etsy's category, who made it and when)...
    expect(text.split("Draft needs info").length - 1).toBe(2);
    expect(text).toContain("Etsy category");
    // ...and so does the banner, in the same words for where.
    expect(text).toMatch(/2 of 2 drafts can.t reach eBay and Etsy yet/);
  });
});

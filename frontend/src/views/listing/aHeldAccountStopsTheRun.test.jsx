/* A run of publishes stops at the first refusal that is about the account.
 *
 * Production's error feed on 2026-09-24 carried eBay error 240 eighteen times
 * in a day from one seller: runs of "Publish selected" into an account eBay was
 * refusing outright. Every draft after the first was refused the same way,
 * each one spending a real publish call, the server's whole 240 diagnosis over
 * again (up to eight more eBay calls), and a refusal card of its own to say
 * the same sentence. The server now marks a finding `every_listing` when it
 * names the ACCOUNT as the cause, and the run stops there; the rest stay
 * drafts, untouched, and the toast says why.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { DraftsStrip } from "@/views/listing/DraftsStrip";
import { blocksEveryListing } from "@/views/listing/publishShared";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: false },
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

function draft(id, title) {
  return {
    id, status: "draft", updated_at: "2026-03-09T00:00:00Z",
    listing: {
      title, price: 24.99, quantity: 1, condition: "USED_EXCELLENT",
      category_id: "11450", images: [`${id}.jpg`],
      package_weight_lb: 1, package_weight_oz: 0,
    },
  };
}

const HELD = {
  published: false, message: "The item cannot be listed or modified.",
  issues: [
    { target: "account", level: "error", every_listing: true,
      title: "eBay is refusing every listing from this account", fix: "…" },
    { target: "account", level: "error", placeholder: true,
      title: "eBay refused this listing and wouldn't say why", fix: "…" },
  ],
};

let host;
let root;
let publishes;

async function mount(listings, answer) {
  publishes = [];
  vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
    const path = String(url);
    if (path === "/api/publish") {
      publishes.push(JSON.parse(opts.body || "{}"));
      return json(answer);
    }
    if (path.startsWith("/api/save/")) return json({ ok: true });
    if (path.startsWith("/api/listings/")) {
      return json(listings.find((l) => path.includes(l.id)) || {});
    }
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true },
                    listings });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({});
  }));
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(<ToastProvider><AppProvider><DraftsStrip /></AppProvider></ToastProvider>);
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

async function click(el) {
  expect(el, "tried to click something that isn't on screen").toBeTruthy();
  await act(async () => { el.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const buttons = () => [...document.body.querySelectorAll("button")];
const button = (text) => buttons().find(
  (b) => (b.textContent || b.getAttribute("aria-label") || "").trim().startsWith(text));
const ticks = () => [...host.querySelectorAll(
  'input[type="checkbox"][aria-label^="Select "]')];

beforeEach(() => { localStorage.clear(); });
afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("a held account stops a run of publishes", () => {
  it("reads the server's mark, and nothing else, as a hold on the account", () => {
    expect(blocksEveryListing(HELD)).toBe(true);
    // The unexplained placeholder alone is NOT a verdict about the account.
    expect(blocksEveryListing({ issues: [HELD.issues[1]] })).toBe(false);
    // A multi-marketplace answer keeps its issues per marketplace.
    expect(blocksEveryListing({ results: { ebay: { issues: [HELD.issues[0]] } } }))
      .toBe(true);
    expect(blocksEveryListing(null)).toBe(false);
  });

  it("sends one publish, not three, and says what it held back", async () => {
    await mount([draft("d1", "Amber Blenko Bud Vase"),
                 draft("d2", "Cobalt Studio Pottery Vase"),
                 draft("d3", "Milk Glass Hobnail Bowl")], HELD);
    for (const t of ticks()) await click(t);
    await click(button("Publish selected (3)"));
    await click(button("Publish live"));
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

    expect(publishes).toHaveLength(1);
    const text = document.body.textContent;
    expect(text).toContain("Stopped there");
    expect(text).toContain("2 more drafts were not sent");
  });

  it("keeps going past a refusal that is only about one listing", async () => {
    await mount([draft("d1", "Amber Blenko Bud Vase"),
                 draft("d2", "Cobalt Studio Pottery Vase")],
                { published: false, issues: [
                  { target: "title", level: "error",
                    title: "eBay is refusing this listing's title" }] });
    for (const t of ticks()) await click(t);
    await click(button("Publish selected (2)"));
    await click(button("Publish live"));
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

    expect(publishes).toHaveLength(2);
  });
});

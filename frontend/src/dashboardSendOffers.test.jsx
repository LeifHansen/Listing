/* "Send offers" is a discount that goes to the watchers and nowhere else.
 *
 * It is the one suggestion group whose work cannot be done in the listing
 * editor. Lowering a price, adding a photo, filling in details — a seller
 * opens the listing and does those. An offer to the people watching an item
 * is a call to eBay's Negotiation API and there is no control in the editor
 * that makes one, so a row whose button walked the seller there would be a
 * button that leads nowhere. Both the group header and the single row send
 * the offer themselves.
 *
 * What the panel says matters as much as what it does. This discount is not
 * the listing's price: the asking price stays exactly where it is and only
 * the buyers already watching see the number. A seller who read this as a
 * markdown would be pricing their store from a figure the public never sees.
 *
 * And the floor is eBay's, not a preference. eBay refuses an offer below 5%
 * off outright, so the input cannot be set lower — a run that fails at the
 * far end of a dozen calls is the worst place to learn it.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { Dashboard } from "@/views/Dashboard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const OFFER_RECS = [
  { listing_id: "a", listing_title: "Nike hoodie", type: "send_offers",
    label: "Send an offer", reason: "4 watchers — offer them a discount before they move on.",
    action: "open", priority: 95 },
  { listing_id: "b", listing_title: "Canon AE-1", type: "send_offers",
    label: "Send an offer", reason: "2 watchers — offer them a discount before they move on.",
    action: "open", priority: 95 },
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

function server(calls, { recs, bulkCaps, groupTotals, result } = {}) {
  return (url, opts = {}) => {
    const path = String(url);
    if (path === "/api/ebay/send-offers") {
      calls.push({ path, body: JSON.parse(opts.body || "{}") });
      return json(result
        || { changed: 2, skipped: 0, failed: 0, deferred: 0, percent: 10 });
    }
    if (path.startsWith("/api/insights")) {
      return json({ recommendations: recs || OFFER_RECS,
                    group_totals: groupTotals || {},
                    finish_all: { total: 0, enrich: 0, accept: 0 },
                    bulk_caps: bulkCaps || {} });
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
  return { root, host, text: () => document.body.textContent || "" };
}

function buttons() {
  return [...document.body.querySelectorAll("button")];
}

function byText(label) {
  return buttons().find((b) => (b.textContent || "").trim() === label);
}

async function click(el) {
  expect(el).toBeTruthy();
  await act(async () => { el.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

async function expand(label) {
  const toggle = buttons().find((b) => (b.textContent || "").includes(label)
                                       && b.getAttribute("aria-expanded") !== null);
  expect(toggle).toBeTruthy();
  if (toggle.getAttribute("aria-expanded") === "true") return;
  await click(toggle);
}

describe("send offers", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("offers the group at the discount the seller picked", async () => {
    const calls = [];
    const { root, text } = await mount(calls, { groupTotals: { send_offers: 2 } });
    await click(byText("Send offers…"));
    await click(buttons().find((b) =>
      (b.textContent || "").startsWith("Offer 2 listings at")));
    expect(calls).toEqual([{ path: "/api/ebay/send-offers",
                             body: { percent: 10, listing_ids: ["a", "b"] } }]);
    expect(text()).toContain("Sent 2 offers at 10% off");
    await act(async () => { root.unmount(); });
  });

  it("says the asking price does not move", async () => {
    /* The whole reason this ranks above a price drop. A seller who read it as
       a markdown would be repricing their store off a number the public never
       sees. */
    const { root, text } = await mount([], { groupTotals: { send_offers: 2 } });
    await click(byText("Send offers…"));
    expect(text()).toContain("your asking price stays as it is");
    await act(async () => { root.unmount(); });
  });

  it("cannot ask for a discount eBay refuses", async () => {
    /* eBay's floor is 5% off and it refuses anything under it. Enforced on
       the input, so the seller is not told at the far end of a dozen calls. */
    const { root } = await mount([], { groupTotals: { send_offers: 2 } });
    await click(byText("Send offers…"));
    const input = document.body.querySelector("input[type=number]");
    expect(input.min).toBe("5");
    expect(input.max).toBe("50");
    await act(async () => { root.unmount(); });
  });

  it("sends one listing's offer from its own row", async () => {
    /* The row does the work. There is nothing in the listing editor that
       sends an offer, so walking the seller there would be a dead end — and
       the panel it opens is about that one listing, not the group. */
    const calls = [];
    const { root, text } = await mount(calls, { groupTotals: { send_offers: 2 } });
    await expand("Send offers");
    await click(byText("Send an offer"));
    expect(text()).toContain("Offer 1 listing at");
    await click(buttons().find((b) =>
      (b.textContent || "").startsWith("Offer 1 listing at")));
    expect(calls).toEqual([{ path: "/api/ebay/send-offers",
                             body: { percent: 10, listing_ids: ["a"] } }]);
    await act(async () => { root.unmount(); });
  });

  it("reports the listings eBay had nobody to offer", async () => {
    /* Ordinary, not an error: the group was computed minutes ago and eBay
       decides per listing whether anyone is interested right now. */
    const { root, text } = await mount([], {
      groupTotals: { send_offers: 2 },
      result: { changed: 1, skipped: 1, failed: 0, deferred: 0, percent: 10 },
    });
    await click(byText("Send offers…"));
    await click(buttons().find((b) =>
      (b.textContent || "").startsWith("Offer 2 listings at")));
    expect(text()).toContain("Sent 1 offer at 10% off");
    expect(text()).toContain("1 skipped");
    await act(async () => { root.unmount(); });
  });

  it("says why a listing was skipped", async () => {
    /* "1 skipped" alone is a button that did nothing and will not say why —
       which is how this was reported. The server sends the reason per
       listing; the toast carries it. */
    const { root, text } = await mount([], {
      groupTotals: { send_offers: 2 },
      result: {
        changed: 1, skipped: 1, failed: 0, deferred: 0, percent: 10,
        results: {
          changed: [{ listing_id: "a", title: "Nike hoodie" }],
          skipped: [{ listing_id: "b", title: "Canon AE-1",
                      message: "eBay has no interested buyers for it right now." }],
          failed: [],
        },
      },
    });
    await click(byText("Send offers…"));
    await click(buttons().find((b) =>
      (b.textContent || "").startsWith("Offer 2 listings at")));
    expect(text()).toContain(
      "Canon AE-1: eBay has no interested buyers for it right now.");
    await act(async () => { root.unmount(); });
  });

  it("names the part of the group one pass reaches", async () => {
    /* Each offer is its own eBay call, so a run is capped and the remainder
       comes back for a second press — said before the seller agrees to it. */
    const { root, text } = await mount([], {
      groupTotals: { send_offers: 3 }, bulkCaps: { send_offers: 2 },
    });
    await click(byText("Send offers…"));
    expect(text()).toContain("One run covers 2 of them");
    await act(async () => { root.unmount(); });
  });

  it("adds the rows that did not fit to what is left", async () => {
    /* The group holds a capped slice of itself, and the server can only
       defer what it was sent. "2 left" has to count both. */
    const { root, text } = await mount([], {
      groupTotals: { send_offers: 4 },
      result: { changed: 1, skipped: 0, failed: 0, deferred: 1, percent: 10 },
    });
    await click(byText("Send offers…"));
    await click(buttons().find((b) =>
      (b.textContent || "").startsWith("Offer 4 listings at")));
    expect(text()).toContain("3 left — run it again to finish");
    await act(async () => { root.unmount(); });
  });

  it("says so when the send could not be made at all", async () => {
    const { root, text } = await mount([], { groupTotals: { send_offers: 2 } });
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({
      ok: false, status: 400,
      headers: { get: () => "application/json" },
      json: () => Promise.resolve({ detail: "Connect eBay first." }),
      text: () => Promise.resolve("{\"detail\":\"Connect eBay first.\"}"),
    })));
    await click(byText("Send offers…"));
    await click(buttons().find((b) =>
      (b.textContent || "").startsWith("Offer 2 listings at")));
    expect(text()).toContain("Couldn't send offers");
    await act(async () => { root.unmount(); });
  });
});

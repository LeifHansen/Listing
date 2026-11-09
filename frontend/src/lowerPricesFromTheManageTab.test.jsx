/* Lower prices, from the Manage tab.
 *
 * Tick one or more live listings, press "Lower prices…", enter a
 * percentage, confirm — and each ticked listing's price comes down by that
 * much and goes straight to eBay. The server runs it as a job, so the bar
 * says which listing it is on while it runs, and the report at the end says
 * what happened to each one in the server's own words, not just a count.
 *
 * This used to be a suggestion on the dashboard: a "Lower prices · N" group
 * built from a listing's age and its view count, with a "Lower all…" on the
 * group. Removed on request — whether a price should come down is the
 * seller's call, not a chore to be nagged about — so the cut lives here, on
 * the listings they ticked, beside End & relist and in the same shape.
 *
 * What this pins on the screen side:
 *
 *  1. The button is on the bar for every seller with ticks, and the request
 *     names exactly the ticked listings and the percentage typed.
 *  2. Nothing is sent until the panel's own submit — the button opens the
 *     panel, and Cancel closes it with nothing sent.
 *  3. The grid reloads from the server's answer and the ticks are cleared.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { ListingsView } from "@/views/ListingsView";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: true, username: "leif" },
  "/api/ebay/policies": { policies: [] },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [
    { key: "ebay", label: "eBay", connected: true, oauth_ready: true, supports: {} },
  ] },
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

const live = (id, title, price = 40) => ({
  id, status: "published", updated_at: "2026-09-01T00:00:00Z",
  listing: { title, description: "Nice.", price, quantity: 1,
    images: [`${id}.jpg`], ebay_listing_id: `1${id}`, source: "ebay" },
});

/* The server: the seller's rows, the job, and its status. The job's answer
   is what the real route produces — a bulk report with a per-listing row for
   everything that was not simply lowered. */
function server(state) {
  return (url, opts = {}) => {
    const path = String(url);
    if (path.startsWith("/api/listings")) {
      state.loads += 1;
      return json({ authed: true, db: { configured: true, connected: true },
                    listings: state.listings });
    }
    if (path === "/api/ebay/lower-prices") {
      state.asked = JSON.parse(opts.body || "{}");
      // What the run does to the store: the ticked prices come down.
      const cut = (p) => Math.round(p * (1 - state.asked.percent / 100) * 100) / 100;
      state.listings = state.listings.map((l) => (state.asked.listing_ids.includes(l.id)
        ? { ...l, listing: { ...l.listing, price: cut(l.listing.price) } } : l));
      return json({ job_id: "job-1", running: true, total: state.asked.listing_ids.length });
    }
    if (path === "/api/bulk/status/job-1") {
      return json({ id: "job-1", done: true, phase: "done",
        current: state.asked.listing_ids.length,
        total_items: state.asked.listing_ids.length,
        result: { percent: state.asked.percent, ...state.result } });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" });
  };
}

let host;
let root;

async function mount(listings, result) {
  const state = { listings, loads: 0, asked: null, result };
  vi.stubGlobal("fetch", vi.fn(server(state)));
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
  return state;
}

const ticks = () => [...host.querySelectorAll("input[type=checkbox][aria-label]")];
const text = () => host.textContent;
const button = (label) => [...host.querySelectorAll("button")]
  .find((b) => (b.textContent || "").includes(label));
const percentBox = () => host.querySelector("input[type=number]");

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
};

describe("Lower prices from the Manage tab", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => {
    await act(async () => { root.unmount(); });
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("lowers the ticked listings by the percentage typed, and reports it", async () => {
    const state = await mount(
      [live("a", "Levi's 501"), live("b", "Buc-ee's Tee"), live("c", "Left alone")],
      { changed: 2, skipped: 0, failed: 0, total: 2,
        results: { changed: [
          { listing_id: "a", title: "Levi's 501", was: 40, now: 31.99 },
          { listing_id: "b", title: "Buc-ee's Tee", was: 40, now: 31.99 },
        ], skipped: [], failed: [] } });
    // The bar, and its button, arrive with the ticks — beside End & relist.
    const [first, second] = ticks();
    expect(text()).not.toContain("Lower prices…");
    await act(async () => { first.click(); });
    await act(async () => { second.click(); });
    expect(text()).toContain("Lower prices…");
    expect(text()).toContain("End & relist (2)");

    // The button opens the amount panel; nothing has been sent yet.
    await act(async () => { button("Lower prices…").click(); });
    await settle();
    expect(state.asked).toBeNull();
    expect(text()).toContain("Lower each ticked price by");
    const box = percentBox();
    expect(box.value).toBe("10");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype, "value").set;
      setter.call(box, "20");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(button("Lower 2 prices by 20%")).toBeTruthy();
    await act(async () => { button("Lower 2 prices by 20%").click(); });
    await settle();

    // Exactly the ticked ones, by id, at the number typed.
    expect(state.asked).toEqual({ percent: 20, listing_ids: ["a", "b"] });
    // The report, in the toast.
    expect(document.body.textContent).toContain("Lowered 2 prices by 20%");
    // The grid reloaded from the server and the ticks are gone.
    expect(state.loads).toBeGreaterThan(1);
    expect(text()).not.toContain("Lower prices…");
    expect(ticks().filter((t) => t.checked)).toEqual([]);
  });

  it("says, per listing, what was skipped or failed and why", async () => {
    await mount(
      [live("a", "Levi's 501"), live("b", "Buc-ee's Tee")],
      { changed: 1, skipped: 1, failed: 0, total: 2,
        results: { changed: [{ listing_id: "a", title: "Levi's 501", was: 40, now: 35.99 }],
          skipped: [{ listing_id: "b", title: "Buc-ee's Tee",
            message: "No longer live on eBay." }],
          failed: [] } });
    const [first, second] = ticks();
    await act(async () => { first.click(); });
    await act(async () => { second.click(); });
    await act(async () => { button("Lower prices…").click(); });
    await settle();
    await act(async () => { button("Lower 2 prices by 10%").click(); });
    await settle();
    const body = document.body.textContent;
    expect(body).toContain("Lowered 1 price by 10% · 1 skipped");
    expect(body).toContain("Buc-ee's Tee: No longer live on eBay.");
  });

  it("sends nothing on Cancel, and keeps the ticks", async () => {
    const state = await mount([live("a", "Levi's 501")], {});
    const [first] = ticks();
    await act(async () => { first.click(); });
    await act(async () => { button("Lower prices…").click(); });
    await settle();
    expect(text()).toContain("Lower 1 price by 10%");
    await act(async () => { button("Cancel").click(); });
    await settle();
    expect(state.asked).toBeNull();
    // The panel is closing (its exit animation may still be in the DOM here,
    // so the button's own state is what says it is shut), the bar stays, and
    // so do the ticks.
    expect(button("Lower prices…").getAttribute("aria-expanded")).toBe("false");
    expect(ticks()[0].checked).toBe(true);
  });

  it("refuses a percentage the server would refuse", async () => {
    const state = await mount([live("a", "Levi's 501")], {});
    await act(async () => { ticks()[0].click(); });
    await act(async () => { button("Lower prices…").click(); });
    await settle();
    const box = percentBox();
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype, "value").set;
      setter.call(box, "90");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(text()).toContain("Enter a number between 1 and 75.");
    const submit = button("Lower 1 price by 90%");
    expect(submit.disabled).toBe(true);
    expect(state.asked).toBeNull();
  });
});

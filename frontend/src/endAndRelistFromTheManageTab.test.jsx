/* End & relist, from the Manage tab.
 *
 * Tick one or more live listings, press "End & relist", confirm — and each
 * one comes off eBay and goes straight back up as a NEW listing with a fresh
 * AI-written title and description. The server runs it as a job, so the bar
 * says which listing it is on while it runs, and the report at the end says
 * what happened to each one in the server's own words, not just a count.
 *
 * Two things this pins on the screen side:
 *
 *  1. The ticks are for every seller. They used to appear only once a second
 *     marketplace was connected (the crosspost was the only thing a tick could
 *     do); an eBay-only seller now gets them too, with End & relist on the bar
 *     and no crosspost button.
 *  2. The request names exactly the ticked listings, the grid reloads from
 *     the server's answer afterwards, and the ticks are cleared — the old
 *     ids are ended and gone from Active, so a selection of them would be a
 *     selection of nothing.
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

const live = (id, title) => ({
  id, status: "published", updated_at: "2026-09-01T00:00:00Z",
  listing: { title, description: "Nice.", price: 24.99, quantity: 1,
    images: [`${id}.jpg`], ebay_listing_id: `1${id}`, source: "ebay" },
});

/* The server: the seller's rows, the job, and its status. The job's answer
   is what the real route produces — a bulk report with a per-listing row for
   everything that was not simply relisted. */
function server(state) {
  return (url, opts = {}) => {
    const path = String(url);
    if (path.startsWith("/api/listings")) {
      state.loads += 1;
      return json({ authed: true, db: { configured: true, connected: true },
                    listings: state.listings });
    }
    if (path === "/api/ebay/end-and-relist") {
      state.asked = JSON.parse(opts.body || "{}").listing_ids;
      // What the run does to the store: the old ones end, the new ones are
      // live under new ids with the fresh title.
      state.listings = [
        ...state.asked.map((id) => ({
          ...live(`new-${id}`, `Fresh ${id}`),
        })),
        ...state.listings.map((l) => (state.asked.includes(l.id)
          ? { ...l, status: "ended" } : l)),
      ];
      return json({ job_id: "job-1", running: true, total: state.asked.length });
    }
    if (path === "/api/bulk/status/job-1") {
      return json({ id: "job-1", done: true, phase: "done",
        current: state.asked.length, total_items: state.asked.length,
        result: state.result });
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

async function confirmWith(label) {
  const dialog = document.querySelector("[role='dialog']");
  expect(dialog, "no confirm dialog").toBeTruthy();
  const b = [...dialog.querySelectorAll("button")]
    .find((x) => (x.textContent || "").trim() === label);
  expect(b, `no "${label}" button in the dialog`).toBeTruthy();
  await act(async () => { b.click(); });
  return dialog;
}

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
};

describe("End & relist from the Manage tab", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => {
    await act(async () => { root.unmount(); });
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("ends the ticked listings, relists them, and reports it", async () => {
    const state = await mount(
      [live("a", "Levi's 501"), live("b", "Buc-ee's Tee"), live("c", "Left alone")],
      { changed: 2, skipped: 0, failed: 0, total: 2,
        results: { changed: [
          { listing_id: "a", title: "Levi's 501", new_id: "new-a" },
          { listing_id: "b", title: "Buc-ee's Tee", new_id: "new-b" },
        ], skipped: [], failed: [] } });
    // An eBay-only seller: the ticks are there, the crosspost is not.
    const [first, second] = ticks();
    expect(first).toBeTruthy();
    expect(text()).not.toContain("End & relist");
    await act(async () => { first.click(); });
    await act(async () => { second.click(); });
    expect(text()).toContain("End & relist (2)");
    expect(text()).not.toContain("Crosspost to Etsy");

    await act(async () => { button("End & relist (2)").click(); });
    const dialog = document.querySelector("[role='dialog']");
    expect(dialog.textContent).toContain("End and relist 2 listings?");
    expect(dialog.textContent).toContain("new listing");
    await confirmWith("End & relist 2");
    await settle();

    // Exactly the ticked ones, by id.
    expect(state.asked).toEqual(["a", "b"]);
    // The report, in the toast.
    expect(document.body.textContent).toContain("Relisted 2 listings with fresh copy");
    // The grid reloaded from the server: the new listings are on Active and
    // the old ones are not, and the ticks are gone with them.
    expect(text()).toContain("Fresh a");
    expect(text()).toContain("Fresh b");
    expect(text()).toContain("Left alone");
    expect(text()).not.toContain("End & relist (");
    expect(ticks().filter((t) => t.checked)).toEqual([]);
  });

  it("says, per listing, what was skipped or failed and why", async () => {
    await mount(
      [live("a", "Levi's 501"), live("b", "Buc-ee's Tee")],
      { changed: 1, skipped: 1, failed: 0, total: 2,
        results: { changed: [{ listing_id: "a", title: "Levi's 501", new_id: "new-a" }],
          skipped: [{ listing_id: "b", title: "Buc-ee's Tee",
            message: "Turns out this one sold on eBay — it's archived under Inactive, so there's nothing to relist." }],
          failed: [] } });
    const [first, second] = ticks();
    await act(async () => { first.click(); });
    await act(async () => { second.click(); });
    await act(async () => { button("End & relist (2)").click(); });
    await confirmWith("End & relist 2");
    await settle();
    const body = document.body.textContent;
    expect(body).toContain("Relisted 1 listing with fresh copy · 1 skipped");
    expect(body).toContain("Buc-ee's Tee: Turns out this one sold on eBay");
  });

  it("names the one listing in the dialog, and does nothing on Cancel", async () => {
    const state = await mount([live("a", "Levi's 501")], {});
    const [first] = ticks();
    await act(async () => { first.click(); });
    await act(async () => { button("End & relist (1)").click(); });
    const dialog = document.querySelector("[role='dialog']");
    expect(dialog.textContent).toContain("End and relist this listing?");
    expect(dialog.textContent).toContain("\"Levi's 501\" comes off eBay");
    await confirmWith("Cancel");
    await settle();
    expect(state.asked).toBeNull();
    expect(text()).toContain("End & relist (1)");
  });
});

/* Suggested actions is a list the seller can finish, in one group, with one
 * button.
 *
 * What it was not. "Fill in details" was a prompt to go and do it by hand —
 * open a listing, wait for the AI to read its photos, save, push, repeat —
 * for an edit that is identical every time and needs no human. Nothing
 * spanned both it and "Check details", so clearing the list meant two
 * motions and a dozen presses. And because the engine rebuilds the list from
 * scratch on every load, a list that could not be finished could only be
 * made to shrink by hiding it: a dismiss on every row, a Clear all, and a
 * "Restore N dismissed" parked in the header so a mis-tap was not a one-way
 * door.
 *
 * Then it was one press in the header over two groups that still read as two
 * jobs — and the button on the group under it, the one actually labelled
 * "all", reached a capped 25 of the 50 rows the payload happened to carry.
 * The seller, on a list of 70: "make the Enrich All button actually enrich
 * them all."
 *
 * So: the two halves of finishing a listing's details are ONE group with one
 * count, its button takes the whole list with no cap and no ids of its own,
 * and the rows are behind the chevron for a seller who would rather go
 * through them one at a time. None of the hiding machinery is left, because
 * the only reason for it was that the work could not be done.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { Dashboard } from "@/views/Dashboard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// The fill half: listings the AI has never read.
const RECS = [
  { listing_id: "a", listing_title: "Nike hoodie", type: "specifics",
    label: "Fill in details", reason: "Some fields buyers filter by are still blank.",
    action: "open", priority: 45, rate: null },
  { listing_id: "b", listing_title: "Canon AE-1", type: "specifics",
    label: "Fill in details", reason: "Some fields buyers filter by are still blank.",
    action: "open", priority: 45, rate: null },
];

// The other half: read already, with notes left that only a person can
// settle. A different question per listing, which is why the merged group
// keeps its rows.
const VERIFY_RECS = [
  { listing_id: "c", listing_title: "Hokusai print", type: "verify",
    label: "Check details", reason: "2 things the AI left for you to check.",
    action: "open", priority: 40, rate: null },
  { listing_id: "d", listing_title: "Levi's 501", type: "verify",
    label: "Check details", reason: "1 thing the AI left for you to check.",
    action: "open", priority: 40, rate: null },
];

// A group with its own capped bulk verb — the price drop still runs a capped
// number per pass, so that arithmetic is tested where it still applies.
const PRICE_RECS = [
  { listing_id: "a", listing_title: "Nike hoodie", type: "lower_price",
    label: "Lower the price", reason: "Live 30 days — a price drop can restart interest.",
    action: "open", priority: 68, rate: null },
  { listing_id: "b", listing_title: "Canon AE-1", type: "lower_price",
    label: "Lower the price", reason: "Live 30 days — a price drop can restart interest.",
    action: "open", priority: 68, rate: null },
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

// The API this screen talks to, plus a recorder for the calls under test.
// `bulkCaps` is what the server says one run of a group's button reaches
// (/api/insights); `statuses` are polls to serve before the finished one, for
// the live progress line.
function server(calls, { jobResult, recs, bulkCaps, groupTotals,
                        statuses, finishAll, tokens } = {}) {
  let polls = 0;
  return (url, opts = {}) => {
    const path = String(url);
    if (path === "/api/ebay/lower-prices") {
      calls.push({ path, body: JSON.parse(opts.body || "{}") });
      return json({ changed: 1, skipped: 0, failed: 0, deferred: 0 });
    }
    if (path === "/api/listings/finish-all") {
      calls.push({ path, body: JSON.parse(opts.body || "{}") });
      return json({ job_id: "job-1", running: true,
                    total: (finishAll || {}).total || 0, deferred: 0 });
    }
    // Recorded so the one test that asserts nothing reaches it can see a
    // regression. The capped, client-named fill is no longer this screen's.
    if (path === "/api/listings/enrich") {
      calls.push({ path, body: JSON.parse(opts.body || "{}") });
      return json({ job_id: "job-1", running: true, total: 1, deferred: 0 });
    }
    if (path.startsWith("/api/bulk/status/")) {
      const pending = statuses && polls < statuses.length ? statuses[polls] : null;
      polls += 1;
      if (pending) return json(pending);
      return json({ id: "job-1", done: true, phase: "done",
                    result: jobResult || { changed: 2, skipped: 0, failed: 0,
                                           total: 2, filled: 7, accepted: 0,
                                           deferred: 0, stopped: "" } });
    }
    if (path.startsWith("/api/insights")) {
      return json({ recommendations: recs || RECS,
                    group_totals: groupTotals || {},
                    finish_all: finishAll || { total: 0, enrich: 0, accept: 0 },
                    bulk_caps: bulkCaps || {} });
    }
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true },
                    listings: [] });
    }
    if (tokens && path.startsWith("/api/tokens")) return json(tokens);
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
  // document.body, not the host: the confirm dialog and the toasts are
  // portals, and both are part of what this screen says.
  return { root, host, text: () => document.body.textContent || "" };
}

/** Every button on the page (the dialog renders into document.body). */
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

/** Open a suggestion group so its rows are on screen — and leave an already
 *  open one open. This used to click the toggle unconditionally, which made
 *  the second call CLOSE the group; the assertion after it passed only
 *  because the rows were still mid-exit-animation in the DOM, so the same
 *  test passed here and failed on CI's timing. */
async function expand(label) {
  const toggle = buttons().find((b) => (b.textContent || "").includes(label)
                                       && b.getAttribute("aria-expanded") !== null);
  expect(toggle).toBeTruthy();
  if (toggle.getAttribute("aria-expanded") === "true") return;
  await click(toggle);
}

/* The two halves, as one group.
 *
 * The seller was looking at "Fill in details · 70" stacked on "Check details
 * · 149": two headers, two counts, a button on one of them, and the same
 * sentence in both — this listing's details aren't finished. The split is
 * real to the engine and is not a decision the seller has to make.
 */
describe("finishing a listing's details is one group", () => {
  const BOTH = { recs: [...RECS, ...VERIFY_RECS],
                 groupTotals: { specifics: 70, verify: 149 },
                 finishAll: { total: 219, enrich: 70, accept: 149 } };

  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("shows one row for both halves, not one each", async () => {
    const { root, text } = await mount([], BOTH);
    expect(text()).toContain("Finish details");
    // The two old headers are gone from the screen — the rows still carry
    // their own per-listing labels, which is where that wording belongs.
    expect(buttons().some((b) => (b.textContent || "").includes("Fill in details")
                                 && b.getAttribute("aria-expanded") !== null))
      .toBe(false);
    expect(buttons().some((b) => (b.textContent || "").includes("Check details")
                                 && b.getAttribute("aria-expanded") !== null))
      .toBe(false);
    await act(async () => { root.unmount(); });
  });

  it("counts both halves in one badge", async () => {
    // 70 + 149. The server counts per type and has no idea they render as
    // one row, so a badge showing either number alone would undercount the
    // thing the button is about to do.
    const { root, host } = await mount([], BOTH);
    const badge = [...host.querySelectorAll("span")].find(
      (el) => (el.textContent || "").trim() === "219");
    expect(badge).toBeTruthy();
    await act(async () => { root.unmount(); });
  });

  it("opens to the line items, from both halves", async () => {
    // The reason the merged group kept a list: half of it is notes only a
    // person can settle, one listing at a time.
    const { root, text } = await mount([], BOTH);
    expect(text()).not.toContain("Nike hoodie");
    await expand("Finish details");
    expect(text()).toContain("Nike hoodie");          // a fill
    expect(text()).toContain("Hokusai print");        // a check
    expect(text()).toContain("2 things the AI left for you to check.");
    await act(async () => { root.unmount(); });
  });

  it("keeps other groups to themselves", async () => {
    const { root, text } = await mount([], {
      recs: [...PRICE_RECS, ...RECS],
      groupTotals: { lower_price: 2, specifics: 2 },
      finishAll: { total: 2, enrich: 2, accept: 0 },
    });
    expect(text()).toContain("Lower prices");
    expect(text()).toContain("Finish details");
    await act(async () => { root.unmount(); });
  });
});

/* "Enrich all" means all of them.
 *
 * It meant "up to BULK_ENRICH_CAP of the ids this screen happens to be
 * holding": the payload carries at most 50 rows per type and the server
 * filled 25 of those, so a list of 70 was three presses — and with the cap
 * set to 1 in an environment, the progress line read "1 of 1 · 69 more after
 * this run". The run this button starts now names no ids and has no cap; the
 * server works the set out from the same ranking the screen renders.
 */
describe("the group's button takes the whole list", () => {
  const PLAN = { recs: [...RECS, ...VERIFY_RECS],
                 finishAll: { total: 308, enrich: 131, accept: 177 },
                 groupTotals: { specifics: 131, verify: 177 } };

  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("sends no ids, and never the capped route", async () => {
    const calls = [];
    const { root } = await mount(calls, PLAN);
    await click(byText("Enrich all"));
    await click(byText("Finish them"));
    expect(calls).toEqual([{ path: "/api/listings/finish-all", body: {} }]);
    await act(async () => { root.unmount(); });
  });

  it("promises the whole list, with no remainder to run again", async () => {
    const { root, text } = await mount([], PLAN);
    await click(byText("Enrich all"));
    expect(text()).toContain("Finish all 308 listings?");
    // The words that belonged to a capped run. There is nothing left over
    // now, so claiming there is would be inventing a second press.
    expect(text()).not.toContain("second run");
    expect(text()).not.toContain("of 308 listings?");
    await act(async () => { root.unmount(); });
  });

  it("prices only the listings it will actually charge for", async () => {
    // 308 listings do not cost 308 fills: the AI has already read 177 of
    // them, and re-reading buys nothing.
    const { root, text } = await mount([], {
      ...PLAN,
      tokens: { enabled: true, total: 900, packs: [], costs: { specifics: 2 } },
    });
    await click(byText("Enrich all"));
    expect(text()).toContain("262 AI tokens");   // 131 x 2, not 308 x 2
    await act(async () => { root.unmount(); });
  });

  it("says what happens to each half before it happens", async () => {
    const { root, text } = await mount([], PLAN);
    await click(byText("Enrich all"));
    expect(text()).toContain("131 listings it hasn't read yet");
    expect(text()).toContain("pushes them straight to the live listing");
    expect(text()).toContain("On the other 177");
    expect(text()).toContain("nothing about them goes to eBay");
    await act(async () => { root.unmount(); });
  });

  it("does nothing at all if the seller backs out", async () => {
    const calls = [];
    const { root } = await mount(calls, PLAN);
    await click(byText("Enrich all"));
    await click(byText("Cancel"));
    expect(calls).toHaveLength(0);
    await act(async () => { root.unmount(); });
  });

  it("reports both halves, and what it could not finish", async () => {
    const { root, text } = await mount([], {
      ...PLAN,
      jobResult: {
        changed: 130, skipped: 1, failed: 0, total: 308, filled: 412,
        accepted: 307, deferred: 0, stopped: "",
        results: {
          changed: [], failed: [],
          skipped: [{ listing_id: "z", title: "Kyrie 5 CNY",
                      message: "This listing's photos aren't on the server anymore." }],
        },
      },
    });
    await click(byText("Enrich all"));
    await click(byText("Finish them"));
    expect(text()).toContain("Filled in 130 listings · 412 details added");
    expect(text()).toContain("307 marked as checked");
    // The honest remainder, named — a listing the fill genuinely could not
    // run on stays on the list, and saying so is the difference between
    // falling short and quietly claiming to have finished.
    expect(text()).toContain("1 still need you");
    expect(text()).toContain("Kyrie 5 CNY: This listing's photos aren't on the server");
    await act(async () => { root.unmount(); });
  });

  it("shows which listing it is on, under the group it belongs to", async () => {
    const { root, text } = await mount([], {
      ...PLAN,
      statuses: [{ id: "job-1", done: false, phase: "finishing", current: 3,
                   total_items: 308, current_title: "Radmor Henley Polo" }],
    });
    await click(byText("Enrich all"));
    await click(byText("Finish them"));
    expect(text()).toContain("Radmor Henley Polo");
    expect(text()).toContain("4 of 308");
    // No "N more after this run": the run has no remainder.
    expect(text()).not.toContain("more after this run");
    await act(async () => { await new Promise((r) => setTimeout(r, 1600)); });
    await act(async () => { root.unmount(); });
  });

  it("stays out of the way when there is nothing left to finish", async () => {
    // Another group's suggestions, and nothing of this one's: no button
    // offering to finish a list that is already finished.
    const { root } = await mount([], {
      recs: PRICE_RECS, groupTotals: { lower_price: 2 },
    });
    expect(byText("Enrich all")).toBeFalsy();
    await act(async () => { root.unmount(); });
  });
});

/* The badge is a COUNT, and /api/insights sends a capped slice of the rows.
 *
 * The seller's report: "the Enrich all counter doesn't decrease as we enrich
 * and update items." The run worked every time — listings filled, revises
 * reached eBay — and the number above the button never moved, because the
 * number was the length of the list that arrived and the list was cut to fit.
 * Fill 25 of 80 and 25 that had been below the cut take their place. So the
 * server counts the group before cutting it (group_totals) and the screen
 * reads THAT. */
describe("a group bigger than the rows it was sent", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("shows what the store holds, not what the payload carried", async () => {
    const { root, host } = await mount([], {
      recs: RECS, groupTotals: { specifics: 80 },
      finishAll: { total: 80, enrich: 80, accept: 0 },
    });
    const badge = [...host.querySelectorAll("span")].find(
      (el) => (el.textContent || "").trim() === "80");
    expect(badge).toBeTruthy();
    await act(async () => { root.unmount(); });
  });

  it("falls back to the rows when the server sends no count", async () => {
    // An older server, or an insights fetch that failed: the group reads
    // exactly as it did before, rather than claiming a size it never got.
    const { root, host } = await mount([], { recs: RECS });
    const badge = [...host.querySelectorAll("span")].find(
      (el) => (el.textContent || "").trim() === "2");
    expect(badge).toBeTruthy();
    await act(async () => { root.unmount(); });
  });

  it("shows the server's count with nothing taken off it here", async () => {
    // The badge used to have this browser's hidden rows subtracted from it,
    // which a count has to do while suggestions can be hidden — and which is
    // most of what made the number hard to trust. Nothing hides now, so the
    // count on screen is the server's answer, unedited, open or closed.
    const { root, host } = await mount([], {
      recs: VERIFY_RECS, groupTotals: { verify: 9 },
    });
    const badge = () => [...host.querySelectorAll("span")].find(
      (el) => (el.textContent || "").trim() === "9");
    expect(badge()).toBeTruthy();
    await expand("Finish details");
    expect(badge()).toBeTruthy();
    await act(async () => { root.unmount(); });
  });
});

/* A capped run still has to say so — and one still exists.
 *
 * The price drop reprices a capped number per pass (BULK_PRICE_CAP) and
 * defers the rest, so the panel that spends it must not promise the badge.
 * This is the arithmetic the fill used to need and no longer does. */
describe("a run that is capped says what it covers", () => {
  const THREE = [
    ...PRICE_RECS,
    { listing_id: "c", listing_title: "Levi's 501", type: "lower_price",
      label: "Lower the price", reason: "Live 30 days — a price drop can restart interest.",
      action: "open", priority: 68, rate: null },
  ];

  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("names the part of the group one pass reaches", async () => {
    const { root, text } = await mount([], {
      recs: THREE, groupTotals: { lower_price: 3 },
      bulkCaps: { lower_price: 2 },
    });
    await click(byText("Lower all…"));
    expect(text()).toContain("One run covers 2 of them");
    expect(text()).toContain("the other 1 stays on the list for a second run");
    await act(async () => { root.unmount(); });
  });

  it("promises the whole group when one pass covers it", async () => {
    const { root, text } = await mount([], {
      recs: PRICE_RECS, groupTotals: { lower_price: 2 },
      bulkCaps: { lower_price: 40 },
    });
    await click(byText("Lower all…"));
    expect(text()).not.toContain("second run");
    await act(async () => { root.unmount(); });
  });
});

/* The list is finished now, not hidden.
 *
 * Every row used to carry an X, the header a "Clear all" beside a "Restore N
 * dismissed" whose N climbed into the hundreds. None of that was really about
 * these suggestions: the engine rebuilds the list from scratch on every load,
 * so a list that could not be finished could only be made to shrink by hiding
 * it, and the way back had to sit on screen forever so a mis-tap was not a
 * one-way door. The button does the work instead, so all of it goes — and
 * the count stops being a number with silent subtractions in it.
 */
describe("nothing on the list is hidden any more", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("gives a row no way to be waved away", async () => {
    const { root, text } = await mount([], { recs: VERIFY_RECS });
    await expand("Finish details");
    expect(text()).toContain("Hokusai print");
    // The row is there to be opened, and that is the only thing on it.
    expect(buttons().find(
      (b) => (b.getAttribute("aria-label") || "").includes("Dismiss")))
      .toBeFalsy();
    await act(async () => { root.unmount(); });
  });

  it("keeps the section header free of controls", async () => {
    // The press that finishes the work sits on the group it finishes. It was
    // in the header while it spanned two groups nothing else could reach;
    // now that they are one row with a button of their own, a second copy
    // naming the same number is how a seller comes to distrust both.
    const { root } = await mount([], {
      recs: VERIFY_RECS, finishAll: { total: 12, enrich: 5, accept: 7 },
    });
    expect(byText("Finish all 12")).toBeFalsy();
    expect(byText("Clear all")).toBeFalsy();
    expect(buttons().find(
      (b) => (b.textContent || "").includes("dismissed"))).toBeFalsy();
    // ...and the one that does the work is on the group.
    expect(byText("Enrich all")).toBeTruthy();
    await act(async () => { root.unmount(); });
  });

  it("ignores what an older build left in this browser", async () => {
    // Sellers who used the dismiss control still have its list in
    // localStorage. It is nobody's reader now, and a row it names must not
    // go on being hidden by a feature that no longer exists.
    localStorage.setItem("thryft-dismissed-recs",
      JSON.stringify(["c|verify", "d|verify"]));
    const { root, text } = await mount([], { recs: VERIFY_RECS });
    await expand("Finish details");
    expect(text()).toContain("Hokusai print");
    expect(text()).toContain("Levi's 501");
    await act(async () => { root.unmount(); });
  });

  it("points a group's own button at the whole group", async () => {
    // Nothing narrows the set behind a group verb any more, so what the
    // button sends is simply what the group holds.
    const calls = [];
    const { root } = await mount(calls, { recs: PRICE_RECS });
    await click(byText("Lower all…"));
    await click(buttons().find(
      (b) => (b.textContent || "").startsWith("Lower 2 prices")));
    expect(calls[0].body.listing_ids).toEqual(["a", "b"]);
    await act(async () => { root.unmount(); });
  });
});

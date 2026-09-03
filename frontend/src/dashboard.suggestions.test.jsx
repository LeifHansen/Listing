/* Suggested actions is a list the seller can act on and a list they own.
 *
 * Two things it was not. "Fill in details" was a prompt to go and do it by
 * hand — open a listing, wait for the AI to read its photos, save, push,
 * repeat — for an edit that is identical every time and needs no human. And
 * nothing on the list could be waved away: the engine rebuilds it from
 * scratch on every load, so advice the seller had already considered and
 * decided against came straight back, and a to-do list that will not shrink
 * stops being read at all.
 *
 * So: the group carries one button that fills every listing in it, and every
 * row carries a dismiss — with a way back, because a mis-tapped X on a
 * one-way door is worse than the nag it removed.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { Dashboard } from "@/views/Dashboard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const RECS = [
  { listing_id: "a", listing_title: "Nike hoodie", type: "specifics",
    label: "Fill in details", reason: "Some fields buyers filter by are still blank.",
    action: "open", priority: 45, rate: null },
  { listing_id: "b", listing_title: "Canon AE-1", type: "specifics",
    label: "Fill in details", reason: "Some fields buyers filter by are still blank.",
    action: "open", priority: 45, rate: null },
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
// (/api/insights); `running` is the job's own report of the split; `statuses`
// are polls to serve before the finished one, for the live progress line.
function server(calls, { jobResult, recs, bulkCaps, running, statuses } = {}) {
  let polls = 0;
  return (url, opts = {}) => {
    const path = String(url);
    if (path === "/api/listings/enrich") {
      calls.push({ path, body: JSON.parse(opts.body || "{}") });
      return json(running
        || { job_id: "job-1", running: true, total: 2, deferred: 0 });
    }
    if (path.startsWith("/api/bulk/status/")) {
      const pending = statuses && polls < statuses.length ? statuses[polls] : null;
      polls += 1;
      if (pending) return json(pending);
      return json({ id: "job-1", done: true, phase: "done",
                    result: jobResult || { changed: 2, skipped: 0, failed: 0,
                                           total: 2, filled: 7, deferred: 0,
                                           stopped: "" } });
    }
    if (path.startsWith("/api/insights")) {
      return json({ recommendations: recs || RECS, bulk_caps: bulkCaps || {} });
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

/** Open a collapsed suggestion group so its rows are on screen. */
async function expand(label) {
  await click(buttons().find((b) => (b.textContent || "").includes(label)
                                    && b.getAttribute("aria-expanded") !== null));
}

describe("filling in a whole group at once", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("offers one button for the group instead of a trip to each listing", async () => {
    const { root, text } = await mount();
    expect(text()).toContain("Fill in details");
    expect(byText("Enrich all")).toBeTruthy();
    await act(async () => { root.unmount(); });
  });

  it("sends the group's listings — and only those — once confirmed", async () => {
    const calls = [];
    const { root, text } = await mount(calls);

    await click(byText("Enrich all"));
    // Every listing it touches spends AI credits, so it says so first.
    expect(text()).toContain("Fill in details on 2 listings?");
    expect(calls).toHaveLength(0);

    await click(byText("Fill them in"));
    expect(calls).toEqual([{ path: "/api/listings/enrich",
                             body: { listing_ids: ["a", "b"] } }]);
    // The job's own report, not a bare "done".
    expect(text()).toContain("Filled in 2 listings · 7 details added");
    await act(async () => { root.unmount(); });
  });

  it("does nothing at all if the seller backs out", async () => {
    const calls = [];
    const { root } = await mount(calls);
    await click(byText("Enrich all"));
    await click(byText("Cancel"));
    expect(calls).toHaveLength(0);
    await act(async () => { root.unmount(); });
  });

  it("says which listings it could not finish rather than claiming success", async () => {
    // A group is whatever the engine grouped a while ago: some of it has
    // sold, lost its photos, or has nothing the photos can answer.
    const { root, text } = await mount([], {
      jobResult: { changed: 1, skipped: 1, failed: 0, total: 2, filled: 3,
                   deferred: 4, stopped: "" },
    });
    await click(byText("Enrich all"));
    await click(byText("Fill them in"));
    expect(text()).toContain("1 need you");
    expect(text()).toContain("4 left — run it again to finish");
    await act(async () => { root.unmount(); });
  });
});

describe("what the AI left for a person", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("is a nudge to look, with no button that would fill nothing", async () => {
    // A price the lookup raised, an edition to check, where it looked: none
    // of it is a blank an item specific answers, so "Enrich all" over it
    // came back "nothing the photos could answer" every time it was pressed.
    const recs = [{ listing_id: "c", listing_title: "Hokusai print", type: "verify",
      label: "Check details", reason: "2 things the AI left for you to check.",
      action: "open", priority: 40, rate: null }];
    const { root, text } = await mount([], { recs });
    expect(text()).toContain("Check details");
    expect(byText("Enrich all")).toBeFalsy();
    await expand("Check details");
    expect(text()).toContain("Hokusai print");
    expect(text()).toContain("2 things the AI left for you to check.");
    await act(async () => { root.unmount(); });
  });

  it("says why each listing the fill could not finish needs a person", async () => {
    // "1 need you" is a count; the seller's question afterwards is "did it
    // do anything?", and the reason the job gave per listing is the answer.
    const { root, text } = await mount([], {
      jobResult: {
        changed: 1, skipped: 1, failed: 0, total: 2, filled: 3, deferred: 0,
        stopped: "",
        results: {
          changed: [{ listing_id: "a", title: "Nike hoodie", added: 3 }],
          skipped: [{ listing_id: "b", title: "Canon AE-1",
                      message: "No eBay category yet — open it and pick one." }],
          failed: [],
        },
      },
    });
    await click(byText("Enrich all"));
    await click(byText("Fill them in"));
    expect(text()).toContain("Filled in 1 listing · 3 details added · 1 need you");
    expect(text()).toContain("Canon AE-1: No eBay category yet");
    await act(async () => { root.unmount(); });
  });
});

/* A group can be bigger than one run. The server fills a capped number of
 * listings per pass and hands the rest back as `deferred` — so a 3-listing
 * group under a cap of 2 asked the seller to confirm 3, quoted the AI cost of
 * 3, then filled 2 and reported "1 of 2" underneath a badge reading 3. The
 * number of listings is the whole content of that dialog; it has to be the
 * one that will actually happen. */
describe("a group bigger than one run", () => {
  const THREE = [
    ...RECS,
    { listing_id: "c", listing_title: "Levi's 501", type: "specifics",
      label: "Fill in details", reason: "Some fields buyers filter by are still blank.",
      action: "open", priority: 45, rate: null },
  ];
  const CAPPED = { recs: THREE, bulkCaps: { specifics: 2 },
                   running: { job_id: "job-1", running: true, total: 2, deferred: 1 } };

  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("asks for the listings this pass will really fill, not the whole group", async () => {
    const { root, text } = await mount([], CAPPED);
    await click(byText("Enrich all"));
    expect(text()).toContain("Fill in details on 2 of 3 listings?");
    expect(text()).toContain("the other 1 stays on the list for a second run");
    await act(async () => { root.unmount(); });
  });

  it("still sends the whole group — the server counts what is left over", async () => {
    // Trimming the list here would cost the seller the "1 left" report: the
    // remainder is counted from what the request NAMED.
    const calls = [];
    const { root } = await mount(calls, CAPPED);
    await click(byText("Enrich all"));
    await click(byText("Fill them in"));
    expect(calls[0].body).toEqual({ listing_ids: ["a", "b", "c"] });
    await act(async () => { root.unmount(); });
  });

  it("accounts for the rest of the group while it runs", async () => {
    const { root, text } = await mount([], {
      ...CAPPED,
      statuses: [{ id: "job-1", done: false, phase: "enriching", current: 0,
                   total_items: 2, current_title: "Nike hoodie" }],
    });
    await click(byText("Enrich all"));
    await click(byText("Fill them in"));
    // "1 of 2" alone, under a badge reading 3, is the contradiction that
    // started this. The line carries the whole group's arithmetic.
    expect(text()).toContain("1 of 2 · 1 more after this run");

    // Let the poll come back done so nothing is left running past the test.
    await act(async () => { await new Promise((r) => setTimeout(r, 1600)); });
    await act(async () => { root.unmount(); });
  });

  it("promises the whole group when one run covers it", async () => {
    // No cap published (an older server, or a failed insights fetch) reads as
    // "no limit known" — the group says what it has always said.
    const { root, text } = await mount();
    await click(byText("Enrich all"));
    expect(text()).toContain("Fill in details on 2 listings?");
    expect(text()).not.toContain("second run");
    await act(async () => { root.unmount(); });
  });
});

describe("dismissing a suggestion", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("takes that row off the list", async () => {
    const { root, text } = await mount();
    await expand("Fill in details");
    expect(text()).toContain("Nike hoodie");

    await click(buttons().find(
      (b) => (b.getAttribute("aria-label") || "").includes("Nike hoodie")));

    expect(text()).not.toContain("Nike hoodie");
    expect(text()).toContain("Canon AE-1");   // its neighbour is untouched
    await act(async () => { root.unmount(); });
  });

  it("stays dismissed when the list is rebuilt", async () => {
    // The whole point: /api/insights has no idea and returns both every time.
    const first = await mount();
    await expand("Fill in details");
    await click(buttons().find(
      (b) => (b.getAttribute("aria-label") || "").includes("Nike hoodie")));
    await act(async () => { first.root.unmount(); });

    const { root, text } = await mount();
    await expand("Fill in details");
    expect(text()).not.toContain("Nike hoodie");
    await act(async () => { root.unmount(); });
  });

  it("can be undone — an X is not a one-way door", async () => {
    const { root, text } = await mount();
    await expand("Fill in details");
    await click(buttons().find(
      (b) => (b.getAttribute("aria-label") || "").includes("Nike hoodie")));
    expect(text()).toContain("Restore 1 dismissed");

    await click(byText("Restore 1 dismissed"));
    await expand("Fill in details");
    expect(text()).toContain("Nike hoodie");
    await act(async () => { root.unmount(); });
  });

  it("keeps the way back when the last suggestion goes", async () => {
    // Gating the section on the VISIBLE list would take the undo away with
    // the thing it undoes, and the seller could never get the list back.
    const { root, text } = await mount();
    await expand("Fill in details");
    for (const title of ["Nike hoodie", "Canon AE-1"]) {
      await click(buttons().find(
        (b) => (b.getAttribute("aria-label") || "").includes(title)));
    }
    expect(text()).toContain("every suggestion is dismissed");
    expect(byText("Restore 2 dismissed")).toBeTruthy();
    await act(async () => { root.unmount(); });
  });

  it("leaves the group action pointed at what is still on the list", async () => {
    const calls = [];
    const { root } = await mount(calls);
    await expand("Fill in details");
    await click(buttons().find(
      (b) => (b.getAttribute("aria-label") || "").includes("Nike hoodie")));

    await click(byText("Enrich all"));
    await click(byText("Fill them in"));
    expect(calls[0].body).toEqual({ listing_ids: ["b"] });
    await act(async () => { root.unmount(); });
  });
});

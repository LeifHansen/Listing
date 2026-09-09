/* Duplicate drafts can be merged from the drafts grid.
 *
 * Asked for as: "merge button is not available from this view. make that so."
 *
 * The merge dialog — which draft merges with which, which is the master,
 * whose entries win — existed, but the only button that opened it was on the
 * bulk queue screen. A seller who noticed the duplicate a day later, on the
 * Sell screen where the drafts actually live, had no way to reach it: the
 * select bar there offered publish and delete and nothing in between.
 *
 * So: the select bar carries Merge into one, armed by a single tick exactly
 * as the queue's is, opening the same dialog on the same questions, and the
 * merge's result lands back in the grid.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { DraftsStrip } from "@/views/listing/DraftsStrip";

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
    id,
    status: "draft",
    updated_at: "2026-03-09T00:00:00Z",
    listing: {
      title, price: 24.99, quantity: 1, condition: "USED_EXCELLENT",
      category_id: "11450", images: [`${id}.jpg`],
      package_weight_lb: 1, package_weight_oz: 0,
    },
  };
}

let host;
let root;
let listings;   // what /api/listings answers — mutable, so a merge can shrink it
let merged;     // the POST /api/listings/merge bodies seen

async function mount(initial, { mergeResult } = {}) {
  listings = [...initial];
  merged = [];
  vi.stubGlobal("fetch", vi.fn((url, init = {}) => {
    const path = String(url);
    if (path === "/api/listings/merge/preview") {
      return json({ ok: true, conflicts: [], auto_filled: [], added_photos: 1 });
    }
    if (path === "/api/listings/merge") {
      const body = JSON.parse(init.body);
      merged.push(body);
      const gone = new Set(body.source_ids);
      listings = listings.filter((l) => !gone.has(l.id));
      return json(mergeResult || {
        ok: true, removed: body.source_ids, added: 1, applied: [],
        listing: { ...initial.find((l) => l.id === body.target_id).listing,
                   images: ["d1.jpg", "img_001.jpg"] },
      });
    }
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
    root.render(
      <ToastProvider><AppProvider><DraftsStrip /></AppProvider></ToastProvider>,
    );
  });
  await settle();
}

async function settle() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

/** Wait, in real time, for `pred` — the dialog leaves through a framer-motion
 *  exit animation, and the merge closes it only after the listings refresh
 *  has landed, so "gone" is a few ticks away rather than one. */
async function until(pred, { ms = 3000 } = {}) {
  const deadline = Date.now() + ms;
  while (!pred() && Date.now() < deadline) {
    await act(async () => { await new Promise((r) => setTimeout(r, 25)); });
  }
  expect(pred()).toBe(true);
}

async function click(el) {
  expect(el, "tried to click something that isn't on screen").toBeTruthy();
  await act(async () => { el.click(); });
  await settle();
}

/** A button anywhere on the page — the dialog renders through a portal onto
 *  document.body, so buttons inside it are not under `host`. */
function button(text) {
  return [...document.querySelectorAll("button")]
    .find((b) => (b.textContent || "").trim().startsWith(text));
}

/** The card shells: the one button per card carrying the card classes. */
function cards() {
  return [...host.querySelectorAll("button")]
    .filter((b) => b.className.includes("rounded-card"));
}

function dialog() {
  return document.querySelector('[role="dialog"]');
}

async function enterSelectMode() {
  await click(button("Select"));
}

beforeEach(() => { localStorage.clear(); });

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  host = null;
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("merging duplicate drafts from the grid", () => {
  it("puts Merge into one on the select bar, armed by a single tick", async () => {
    await mount([draft("d1", "Amber Blenko Bud Vase"),
                 draft("d2", "Amber Blenko Vase (second photo set)")]);
    expect(button("Merge into one")).toBeFalsy();   // select mode's, like the rest

    await enterSelectMode();
    const merge = button("Merge into one");
    expect(merge).toBeTruthy();
    expect(merge.disabled).toBe(true);              // nothing ticked yet

    await click(cards()[0]);
    // One tick arms it — the dialog asks what it merges with. Requiring two
    // ticks here when the queue asks for one would be the same button with
    // two rules.
    expect(button("Merge into one").disabled).toBe(false);
  });

  it("with one draft ticked, asks which other draft it merges with", async () => {
    await mount([draft("d1", "Amber Blenko Bud Vase"),
                 draft("d2", "Amber Blenko Vase (second photo set)"),
                 draft("d3", "Cobalt Studio Pottery Vase")]);
    await enterSelectMode();
    await click(cards()[0]);
    await click(button("Merge into one"));

    const d = dialog();
    expect(d).toBeTruthy();
    expect(d.textContent).toContain("Which listing should it merge with?");
    // Every OTHER draft on screen is offered as a partner; the ticked one is
    // what they'd merge with, not a candidate for itself.
    expect(d.textContent).toContain("Amber Blenko Vase (second photo set)");
    expect(d.textContent).toContain("Cobalt Studio Pottery Vase");
    expect(d.querySelectorAll('input[type="checkbox"]')).toHaveLength(2);
  });

  it("with two ticked, opens straight on the master question", async () => {
    await mount([draft("d1", "Amber Blenko Bud Vase"),
                 draft("d2", "Amber Blenko Vase (second photo set)")]);
    await enterSelectMode();
    await click(cards()[0]);
    await click(cards()[1]);
    await click(button("Merge into one"));

    expect(dialog()?.textContent).toContain("Which draft is the master?");
  });

  it("merges by listing id and takes the result back into the grid", async () => {
    await mount([draft("d1", "Amber Blenko Bud Vase"),
                 draft("d2", "Amber Blenko Vase (second photo set)")]);
    await enterSelectMode();
    await click(cards()[0]);
    await click(cards()[1]);
    await click(button("Merge into one"));
    // The first ticked draft is the master by default; the review step asks
    // the server, which here finds nothing clashing.
    await click(button("Next: check the fields"));
    expect(dialog()?.textContent).toContain("Nothing clashes");
    await click(button("Merge 2 drafts"));
    await until(() => !dialog());

    // The server was asked to merge THESE listings, by the ids the rest of
    // the app knows them by. The dialog keys on session_id; the saved
    // listing's id is that session id, and the request must say so.
    expect(merged).toEqual([{
      target_id: "d1", source_ids: ["d2"], field_choices: {},
    }]);
    // The duplicate is gone from the grid — not left there ticked, a click
    // from publishing a listing that no longer exists.
    expect(dialog()).toBeFalsy();
    expect(host.textContent).toContain("Drafts (1)");
    expect(host.textContent).not.toContain("second photo set");
    expect(host.textContent).toContain("(1 of 1)");
    // And it says what happened.
    expect(document.body.textContent).toContain('Merged into "Amber Blenko Bud Vase"');
  });

  it("says so when there is nothing to merge with", async () => {
    await mount([draft("d1", "Amber Blenko Bud Vase")]);
    await enterSelectMode();
    await click(cards()[0]);
    await click(button("Merge into one"));

    expect(dialog()).toBeFalsy();
    expect(document.body.textContent).toContain("There's no other draft to merge with.");
  });
});

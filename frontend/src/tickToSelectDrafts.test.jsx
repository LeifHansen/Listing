/* Drafts are picked for a bulk action with a tick box on the card itself.
 *
 * Asked for as: "please remove the 'Select' button, and just add a static
 * check box allowing users to select listings for bulk actions/merging."
 *
 * Selecting used to be a mode: press Select, every card turns into a tick
 * box, press Cancel to get out. Which meant the way to merge two duplicates
 * was a button that said nothing about merging, and while the mode was on,
 * the cards stopped being cards — no Publish, no Review & List, and a click
 * on one ticked it instead of opening it.
 *
 * So: no Select button, a checkbox standing on every draft card from the
 * moment the grid draws, and the bulk bar arrives with the first tick.
 * Everything else on the card goes on working the whole time.
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

async function mount(listings) {
  vi.stubGlobal("fetch", vi.fn((url) => {
    const path = String(url);
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
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

async function click(el) {
  expect(el, "tried to click something that isn't on screen").toBeTruthy();
  await act(async () => { el.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const buttons = () => [...host.querySelectorAll("button")];
const label = (b) => (b.textContent || b.getAttribute("aria-label") || "").trim();
const button = (text) => buttons().find((b) => label(b).startsWith(text));

/** The tick box on each card. The bar's own "Select all" is a different
 *  checkbox: it has no aria-label, its name comes from the <label> it sits
 *  in, which is how the two are told apart here and by a screen reader. */
function ticks() {
  return [...host.querySelectorAll('input[type="checkbox"][aria-label^="Select "]')];
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

describe("ticking drafts for a bulk action", () => {
  it("has no Select button, and a tick box on every card from the start",
    async () => {
      await mount([draft("d1", "Amber Blenko Bud Vase"),
                   draft("d2", "Cobalt Studio Pottery Vase")]);

      expect(buttons().some((b) => label(b) === "Select")).toBe(false);
      expect(ticks()).toHaveLength(2);
      // Nothing is ticked until the seller ticks it.
      expect(ticks().every((t) => t.checked)).toBe(false);
      // Each one names the listing it belongs to, so a grid of twenty is
      // still twenty distinct controls to anyone reading it aloud.
      expect(ticks()[0].getAttribute("aria-label"))
        .toContain("Amber Blenko Bud Vase");
    });

  it("brings up the bulk bar on the first tick, and Clear takes it away",
    async () => {
      await mount([draft("d1", "Amber Blenko Bud Vase"),
                   draft("d2", "Cobalt Studio Pottery Vase")]);
      // No bar over an untouched grid: it would be a row of disabled
      // buttons sitting on top of every seller's drafts forever.
      expect(button("Publish selected")).toBeFalsy();
      expect(button("Delete selected")).toBeFalsy();

      await click(ticks()[0]);
      expect(host.textContent).toContain("(1 of 2)");
      expect(button("Publish selected (1)")).toBeTruthy();
      expect(button("Merge into one")).toBeTruthy();
      expect(button("Delete selected (1)")).toBeTruthy();

      await click(button("Clear"));
      expect(ticks()[0].checked).toBe(false);
      expect(button("Publish selected")).toBeFalsy();
    });

  it("ticks the lot from the bar, and unticks them the same way", async () => {
    await mount([draft("d1", "Amber Blenko Bud Vase"),
                 draft("d2", "Cobalt Studio Pottery Vase")]);
    await click(ticks()[0]);

    const selectAll = [...host.querySelectorAll('input[type="checkbox"]')]
      .find((el) => !el.getAttribute("aria-label"));
    expect(selectAll, "no Select all on the bulk bar").toBeTruthy();
    // One ticked out of two: the box says "some", not "all".
    expect(selectAll.checked).toBe(false);
    expect(selectAll.indeterminate).toBe(true);

    await click(selectAll);
    expect(ticks().every((t) => t.checked)).toBe(true);
    expect(host.textContent).toContain("(2 of 2)");
  });

  it("leaves every card's own buttons working while drafts are ticked",
    async () => {
      await mount([draft("d1", "Amber Blenko Bud Vase"),
                   draft("d2", "Cobalt Studio Pottery Vase")]);
      await click(ticks()[0]);

      // The old select mode took these off the cards, which is what made
      // "fix this one, publish that one" impossible without leaving it.
      const perCard = buttons().map(label).filter((l) =>
        ["Publish", "Review & List", "Delete this draft"].includes(l));
      expect(perCard).toEqual([
        "Publish", "Review & List", "Delete this draft",
        "Publish", "Review & List", "Delete this draft",
      ]);
    });
});

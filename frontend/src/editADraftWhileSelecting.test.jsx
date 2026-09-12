/* A draft can be opened and edited while others are ticked for a bulk action.
 *
 * Asked for as: "allow users to edit individual items from this view."
 *
 * Selecting used to be a mode that turned every card into a tick box, which
 * took away the one thing a seller sorting thirteen drafts most needs when
 * they spot a wrong price on one of them: opening it. They had to leave
 * select mode, open the draft, come back, and tick everything again, because
 * the ticks lived inside the grid component and opening a draft unmounts it.
 *
 * So: ticking is its own control on the card (see tickToSelectDrafts) and
 * the card itself still opens, ticking and editing stay separate actions,
 * and the ticks are held in the app store so they are still there when the
 * seller comes back.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
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
      category_id: "11450", images: ["a.jpg"],
      package_weight_lb: 1, package_weight_oz: 0,
    },
  };
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

/** The provider tree, with the drafts grid mounted or not.
 *
 * `grid=false` then true again is what opening a draft does for real: the
 * editor replaces the Sell screen, so DraftsStrip unmounts while the app
 * context around it stays put. Re-rendering the same root is how the test
 * takes that trip without needing a ref to reach inside the tree.
 */
function Tree({ onValue, grid = true }) {
  return (
    <ToastProvider><AppProvider>
      <Probe onValue={onValue} />
      {grid && <DraftsStrip />}
    </AppProvider></ToastProvider>
  );
}

let host;
let root;
let app;
const observe = (v) => { app = v; };

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
  await act(async () => { root.render(<Tree onValue={observe} />); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

/** Off to the editor and back — the grid unmounts, the app context doesn't. */
async function leaveAndComeBack() {
  await act(async () => { root.render(<Tree onValue={observe} grid={false} />); });
  // Proven, not assumed: if the grid were still mounted its state would
  // survive for the boring reason, and the test would pass vacuously.
  expect(host.textContent).not.toContain("Amber Blenko");
  await act(async () => { root.render(<Tree onValue={observe} />); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

/** The card shells: the one button per card carrying the card classes. */
function cards() {
  return [...host.querySelectorAll("button")]
    .filter((b) => b.className.includes("rounded-card"));
}

/** The tick box on each card, which picks a draft for a bulk action without
 *  going anywhere near the editor. */
function ticks() {
  return [...host.querySelectorAll('input[type="checkbox"][aria-label^="Select "]')];
}

async function click(el) {
  expect(el, "tried to click something that isn't on screen").toBeTruthy();
  await act(async () => { el.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

beforeEach(() => { localStorage.clear(); });

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  host = null;
  app = null;
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("editing one draft out of a selection", () => {
  it("opens the draft when its card is clicked, ticked or not", async () => {
    await mount([draft("d1", "Amber Blenko Bud Vase"),
                 draft("d2", "Cobalt Studio Pottery Vase")]);
    await click(ticks()[0]);
    await click(cards()[1]);

    // The editor is open on the SECOND card...
    expect(app.session?.sessionId).toBe("d2");
    // ...and opening it neither ticked it nor disturbed the tick already on
    // the first: ticking and editing are separate actions, or one of them is
    // always an accident.
    expect(app.draftSelection).toEqual({ d1: true });
  });

  it("ticks the card without opening it when the tick box is clicked",
    async () => {
      await mount([draft("d1", "Amber Blenko Bud Vase")]);
      await click(ticks()[0]);

      expect(app.draftSelection).toEqual({ d1: true });
      expect(app.session).toBeFalsy();
    });

  it("keeps the ticks while the seller is away editing one", async () => {
    // The reason this was impossible: opening a draft unmounts the grid, and
    // the ticks used to live inside it. Twelve of thirteen ticked, one opened
    // to fix, and the other eleven were gone.
    await mount([draft("d1", "Amber Blenko Bud Vase"),
                 draft("d2", "Cobalt Studio Pottery Vase")]);
    await click(ticks()[0]);
    expect(app.draftSelection).toEqual({ d1: true });

    await leaveAndComeBack();

    expect(app.draftSelection).toEqual({ d1: true });
    expect(ticks()[0].checked).toBe(true);
    expect(host.textContent).toContain("(1 of 2)");
  });
});

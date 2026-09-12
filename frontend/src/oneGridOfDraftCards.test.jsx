/* A batch is reviewed in the same grid as every other draft — and saving one
 * comes back to it.
 *
 * Reported as: "when importing a batch, and editing one of the items, when I
 * hit save, it brings me back to a slightly different grid view of item
 * cards. I want it to match exactly."
 *
 * Both halves of that were real. The batch screen drew its own grid of item
 * cards — its own tile with the title and the price as text boxes, its own
 * columns, its own Publish / Delete / Merge buttons, its own selection —
 * while the Sell screen drew the drafts grid: a photo tile with the AI's
 * confidence, the review count, the price badge, and Publish / Review & List
 * under it. Two screens, two grids, the same five listings. And a clean draft
 * save from a batch item called openListings("drafts"), which does not go
 * back to the batch at all: it leaves for the Sell overview, upload box and
 * all, which is the screen the report's screenshot was taken on.
 *
 * So: the batch screen renders the drafts grid (scoped to its own ids), and a
 * save closes the editor back onto the screen it was opened from. What this
 * pins is that the two grids are the SAME grid — mount the batch screen and
 * the Sell screen over the same drafts and the markup has to match — and that
 * the round trip through the editor ends where it started.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { NewListing } from "@/views/NewListing";
import { DraftsStrip } from "@/views/listing/DraftsStrip";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

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

function row(id, title, price) {
  return {
    id,
    status: "draft",
    updated_at: "2026-03-09T00:00:00Z",
    listing: {
      title, price, quantity: 1, condition: "USED_EXCELLENT",
      category_id: "11450", category_suggestion: "Clothing > Polos",
      images: ["img_000.jpg"], ai_confidence: "high",
      package_weight_lb: 1, package_weight_oz: 0,
      fulfillment_policy_id: "fp1",
    },
  };
}

const LISTINGS = [row("s1", "Jones Soda Co Polo Shirt", 33.99),
                  row("s2", "Swannies Golf Polo Shirt", 31.99)];

// The batch job that drafted exactly those two.
const JOB = {
  id: "job1", done: true, phase: "done", current: 2, total_items: 2,
  total_photos: 5,
  items: LISTINGS.map((l) => ({
    session_id: l.id, status: "draft", title: l.listing.title,
    listing: l.listing,
  })),
};

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

let host;
let root;
let app;

function stub({ saved = () => json({}) } = {}) {
  vi.stubGlobal("fetch", vi.fn((url) => {
    const path = String(url);
    if (path.startsWith("/api/bulk/status/")) return json(JOB);
    if (path.startsWith("/api/publish/")) return saved();
    if (path.startsWith("/api/listings/")) {
      return json(LISTINGS.find((l) => path.includes(l.id)) || {});
    }
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true },
                    listings: LISTINGS });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" });
  }));
}

async function render(node) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider>
        <Probe onValue={(v) => { app = v; }} />
        {node}
      </AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const text = () => host.textContent || "";

/** The grid of draft cards, as markup.
 *
 * Inline styles come off first: framer-motion writes the entry animation
 * into them, so two grids mounted a few milliseconds apart differ there and
 * nowhere that a seller can see.
 */
function gridMarkup() {
  const wrapper = [...host.querySelectorAll("div")].find(
    (d) => typeof d.className === "string"
      && /(^|\s)grid(\s|$)/.test(d.className)
      && [...d.querySelectorAll("button")].some(
        (b) => typeof b.className === "string"
          && b.className.includes("rounded-card")));
  if (!wrapper) return null;
  const clone = wrapper.cloneNode(true);
  clone.querySelectorAll("*").forEach((el) => el.removeAttribute("style"));
  return `${wrapper.className}\n${clone.innerHTML}`;
}

/** Every card's action row, by label — Publish, Review & List, Delete. */
function cardActions() {
  return [...host.querySelectorAll("button")]
    .map((b) => (b.textContent || b.getAttribute("aria-label") || "").trim())
    .filter((label) => ["Publish", "Review & List", "Delete this draft"]
      .includes(label));
}

describe("one grid of draft cards", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => {
    if (root) await act(async () => { root.unmount(); });
    root = null;
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("draws the batch review with the Sell screen's own grid", async () => {
    // The Sell screen's drafts grid, over these two drafts.
    stub();
    await render(<DraftsStrip />);
    const sellScreen = gridMarkup();
    const sellActions = cardActions();
    expect(sellScreen).toBeTruthy();
    expect(text()).toContain("Drafts (2)");
    // Every card carries the pair the report's screenshot shows under each
    // tile, so "the same grid" is being compared against something real.
    expect(sellActions).toEqual([
      "Publish", "Review & List", "Delete this draft",
      "Publish", "Review & List", "Delete this draft",
    ]);

    await act(async () => { root.unmount(); });
    root = null;
    document.body.innerHTML = "";

    // Now the batch screen, over the batch that drafted them.
    await render(<NewListing />);
    await act(async () => { app.startBulk("job1"); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(text()).toContain("Bulk listing");          // really the batch screen
    expect(text()).toContain("2 items queued as drafts");
    // …and the cards in it are the Sell screen's, to the markup.
    expect(gridMarkup()).toBe(sellScreen);
    expect(cardActions()).toEqual(sellActions);
    // The batch's own ending, which the Sell screen has no business offering.
    expect(text()).toContain("Publish all (2)");
  });

  it("comes back to the batch when a draft saved from it", async () => {
    // A save with nothing wrong with it: the server takes the draft and
    // reports no issues, which is the "done editing" case that used to jump
    // to the Sell overview.
    stub({ saved: () => json({ ok: true, published: false,
                               message: "Draft saved" }) });
    await render(<NewListing />);
    await act(async () => { app.startBulk("job1"); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(text()).toContain("Bulk listing");

    // Open one item, the way its card's "Review & List" does.
    await act(async () => { await app.openListing("s1"); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(text()).toContain("Back to batch");         // the editor is up

    // Save Draft.
    const save = [...host.querySelectorAll("button")]
      .find((b) => (b.textContent || "").includes("Save Draft"));
    expect(save).toBeTruthy();
    await act(async () => { save.click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

    // Back on the batch screen — not the Sell overview, which announces
    // itself with the upload box and the finished-batch banner.
    expect(text()).toContain("2 items queued as drafts");
    expect(text()).toContain("Publish all (2)");
    expect(text()).not.toContain("Drag photos here");
    expect(text()).not.toContain("tap to review the results");
    // And nothing asked for a screen change on the way: a save from the
    // batch leaves no pending listings-jump behind (see store.openListings),
    // which is what used to sit there until the next thing that reads it —
    // the batch settling itself again on the way back in — decided the
    // seller had asked for the lists.
    expect(app.listingsJumpRef.current).toBe(null);
  });
});

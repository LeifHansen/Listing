/* A draft that goes live leaves the drafts grid — visibly.
 *
 * Publishing already flipped the record's status, so the card was gone from
 * the grid in the same frame the server answered. On a bulk run that made the
 * grid shrink silently under the seller's cursor, and what it left behind —
 * the drafts eBay refused, the ones still missing a field — looked exactly
 * like what a run that published nothing would leave. There was no moment
 * that said "that one made it".
 *
 * These pin both halves: the send-off is played (the card is held in place,
 * announced live, and only then lifts off) and the card really is gone
 * afterwards, while a draft that could not publish stays exactly where it is.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { DraftsStrip } from "@/views/listing/DraftsStrip";
import {
  CELEBRATE_EXIT_MS, CELEBRATE_HOLD_MS, liveLabel, publishedCardMotion,
  withCelebrating,
} from "@/views/listing/publishCelebration";

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

// Everything eBay demands of a new listing (see blockers.js), so the card's
// Publish button is live rather than disabled.
function ready(id, title, updated = "2026-03-09T00:00:00Z") {
  return {
    id,
    status: "draft",
    updated_at: updated,
    listing: {
      title, price: 24.5, quantity: 1, condition: "USED_EXCELLENT",
      category_id: "11450", images: ["a.jpg"],
      package_weight_lb: 1, package_weight_oz: 0,
    },
  };
}

// Same listing with no price: eBay would refuse it, so it must still be on
// screen when the batch is done.
function blocked(id, title, updated = "2026-03-08T00:00:00Z") {
  const item = ready(id, title, updated);
  return { ...item, listing: { ...item.listing, price: null } };
}

function server(state) {
  return (url, opts = {}) => {
    const path = String(url);
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true },
                    listings: state.listings });
    }
    if (path.startsWith("/api/save/")) return json({ ok: true });
    if (path === "/api/publish") {
      const id = JSON.parse(opts.body || "{}").session_id;
      // What the server does on a real publish: the record is live from here
      // on, so the refresh that follows must not hand the draft back.
      state.listings = state.listings.map(
        (l) => (l.id === id ? { ...l, status: "published" } : l));
      return json({ published: true, listing_id: "110123456789",
                    message: "Published! It's live now." });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({});
  };
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

async function mount(listings) {
  const state = { listings };
  vi.stubGlobal("fetch", vi.fn(server(state)));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let app = null;
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <Probe onValue={(v) => { app = v; }} />
          <DraftsStrip />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  return {
    root,
    state,
    app: () => app,
    text: () => host.textContent || "",
    // Every button whose label contains `label`, in DOM order.
    buttons: (label) => [...host.querySelectorAll("button")]
      .filter((b) => (b.textContent || "").includes(label)),
    tick: async (ms) => {
      await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
    },
  };
}

// Publish the nth card, answering the confirm dialog that guards it.
async function publishCard(ui, index = 0) {
  await act(async () => { ui.buttons("Publish")[index].click(); });
  const confirmButton = [...document.querySelectorAll("button")]
    .find((b) => (b.textContent || "").trim() === "Publish live");
  expect(confirmButton).toBeTruthy();
  await act(async () => { confirmButton.click(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
}

describe("a draft that publishes leaves the drafts grid", () => {
  beforeEach(() => { localStorage.clear(); vi.useFakeTimers(); });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("holds the card for its send-off, then takes it off the grid", async () => {
    const ui = await mount([ready("d1", "Enamel Coffee Pot")]);
    expect(ui.text()).toContain("Enamel Coffee Pot");

    await publishCard(ui);

    // Still on screen, and saying why: the whole point of the beat is that
    // the seller sees WHICH draft made it before it goes.
    expect(ui.text()).toContain("Enamel Coffee Pot");
    expect(ui.text()).toContain("Live on eBay!");
    // ...and the record is already live underneath, exactly as before — the
    // hold is visual, so an interrupted animation cannot strand a published
    // listing in Drafts.
    expect(ui.app().listingsState.items[0].status).toBe("published");

    await ui.tick(CELEBRATE_HOLD_MS + CELEBRATE_EXIT_MS + 50);
    expect(ui.text()).not.toContain("Enamel Coffee Pot");
    expect(ui.text()).not.toContain("Live on eBay!");

    await act(async () => { ui.root.unmount(); });
  });

  it("leaves the drafts that still need work, and only those", async () => {
    // What a batch looks like when it lands: one published, one eBay would
    // refuse for a missing price.
    const ui = await mount([
      ready("d1", "Enamel Coffee Pot"),
      blocked("d2", "Flamingo Camp Shirt"),
    ]);
    expect(ui.text()).toContain("Drafts (2)");

    await publishCard(ui);
    await ui.tick(CELEBRATE_HOLD_MS + CELEBRATE_EXIT_MS + 50);

    expect(ui.text()).not.toContain("Enamel Coffee Pot");
    expect(ui.text()).toContain("Flamingo Camp Shirt");
    expect(ui.text()).toContain("Keeping this off eBay");
    expect(ui.text()).toContain("Drafts (1)");

    await act(async () => { ui.root.unmount(); });
  });

  it("counts the card it is still showing", async () => {
    // The count is read off the grid, not off the store: dropping to "(0)" a
    // second before the last card left read as a miscount.
    const ui = await mount([ready("d1", "Enamel Coffee Pot")]);
    await publishCard(ui);
    expect(ui.text()).toContain("Drafts (1)");

    await ui.tick(CELEBRATE_HOLD_MS + CELEBRATE_EXIT_MS + 50);
    // The last draft: the strip renders nothing at all once it has gone.
    expect(ui.text()).toBe("");

    await act(async () => { ui.root.unmount(); });
  });
});

describe("the grid a send-off is played in", () => {
  const item = (id) => ({ id, listing: { title: id } });

  it("puts a published card back where it was standing", () => {
    // Publishing patches the record immediately, so the card is out of
    // `drafts` before the animation has started. Appending it would play the
    // send-off at the end of the grid, on top of somebody else's card.
    const drafts = [item("a"), item("c")];
    const celebrating = { b: { id: "b", item: item("b"), index: 1, phase: "burst" } };
    expect(withCelebrating(drafts, celebrating).map((d) => d.id))
      .toEqual(["a", "b", "c"]);
  });

  it("never shows a card twice", () => {
    // The refresh can still be carrying the draft while the send-off plays.
    const drafts = [item("a"), item("b")];
    const celebrating = { b: { id: "b", item: item("b"), index: 1, phase: "burst" } };
    expect(withCelebrating(drafts, celebrating).map((d) => d.id)).toEqual(["a", "b"]);
  });

  it("leaves an ordinary grid untouched", () => {
    const drafts = [item("a"), item("b")];
    expect(withCelebrating(drafts, {})).toBe(drafts);
  });

  it("only names eBay when eBay is where it went", () => {
    // A publish can fan out — or go to Etsy alone. `null` is the eBay-only
    // seller, which is most of them and the one case that can name a store.
    expect(liveLabel(null)).toBe("Live on eBay!");
    expect(liveLabel(["ebay"])).toBe("Live on eBay!");
    expect(liveLabel(["ebay", "etsy"])).toBe("It's live!");
    expect(liveLabel(["etsy"])).toBe("It's live!");
  });

  it("ends on invisible, however the seller has their motion set", () => {
    for (const reduced of [false, true]) {
      expect(publishedCardMotion("leaving", { reduced }).animate.opacity).toBe(0);
      expect(publishedCardMotion("burst", { reduced }).animate.opacity).toBe(1);
      expect(publishedCardMotion(undefined, { reduced }).animate.opacity).toBe(1);
    }
  });
});

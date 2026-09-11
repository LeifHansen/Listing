/* One item the server couldn't rebuild must not blank the whole batch.
 *
 * A batch interrupted by a restart is picked back up and its finished items
 * are rebuilt from disk (main._bulk_items_from_disk). An item whose
 * listing.json did not survive came back as `listing: null` with its status
 * still "draft" — and the batch screen's render reads every draft's blockers
 * straight off its listing. `ebayBlockers(null)` threw, and because that call
 * runs in the SCREEN's render rather than a card's, the throw unmounted the
 * whole tree: the seller lost a screen full of perfectly good drafts to the
 * error boundary and saw "This screen ran into a problem".
 *
 * The server no longer sends that shape (a listing neither disk nor the
 * database can produce goes out as the failure it is), but the screen must
 * survive it regardless — a batch is not something to lose over one item, and
 * the shape can still arrive from a client mid-upgrade. It can arrive from
 * either side now, too: the batch job says which sessions the run touched,
 * and the store says what each one holds, so the empty listing is tested
 * where the render actually reads it.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { BulkQueue } from "@/views/listing/BulkMode";

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

function server(status, listings) {
  return (url) => {
    const path = String(url);
    if (path.startsWith("/api/bulk/status/")) return json(status);
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true },
                    listings });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" });
  };
}

async function mount(status, listings = []) {
  vi.stubGlobal("fetch", vi.fn(server(status, listings)));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <BulkQueue jobId="job1" onExit={() => {}} onSettled={() => {}} />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, text: () => document.body.textContent || "" };
}

/** What the batch is showing, as the seller reads it: one card per draft,
 *  titled. The cards are the drafts grid's (see DraftsStrip) — the same tile
 *  the Sell screen draws — so the title is read off the card, not out of the
 *  text box the batch screen used to put it in. */
function titles() {
  return [...document.body.querySelectorAll("button")]
    .filter((b) => b.className.includes("rounded-card"))
    .map((b) => b.querySelector(".line-clamp-2")?.textContent || "");
}

/** The saved row the batch's session ids resolve against. */
function row(id, title, extra = {}) {
  return {
    id,
    status: "draft",
    updated_at: "2026-03-09T00:00:00Z",
    listing: {
      title, price: 24.99, quantity: 1, condition: "USED_EXCELLENT",
      category_id: "11450", images: ["img_000.jpg"],
      package_weight_lb: 1, package_weight_oz: 0,
    },
    ...extra,
  };
}

const survived = {
  session_id: "s2", status: "draft", title: "Canon AE-1",
  listing: { title: "Canon AE-1", price: 90, images: ["img_000.jpg"],
             condition: "USED_EXCELLENT" },
};

function finished(items) {
  return { id: "job1", done: true, phase: "done", current: items.length,
           total_items: items.length, total_photos: 4, items };
}

describe("a bulk item with no listing", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("does not take the rest of the batch down with it", async () => {
    // The exact shape the resumed batch used to send: a draft with no
    // listing at all, sitting beside one that came back fine — and a stored
    // row in the same state, which is what the cards actually read.
    const lost = { session_id: "s1", status: "draft", title: "item 1",
                   listing: null };
    const { root, text } = await mount(
      finished([lost, survived]),
      [row("s1", "", { listing: null }), row("s2", "Canon AE-1")]);
    // Rendering at all is the assertion — before the fix this threw out of
    // act() and never got here. Both cards are on screen: the one with no
    // listing behind it (which has nothing to title itself with) and the one
    // that came back whole.
    expect(titles()).toEqual(["(untitled)", "Canon AE-1"]);
    expect(text()).toContain("Drafts (2)");
    await act(async () => { root.unmount(); });
  });

  it("survives it while the batch is still running", async () => {
    // The same item arriving on a poll rather than in the final payload:
    // `blocked` is computed on every render, not only the last one.
    const lost = { session_id: "s1", status: "draft", title: "item 1",
                   listing: null };
    const { root } = await mount({
      id: "job1", done: false, phase: "identifying", current: 2,
      total_items: 6, total_photos: 12, items: [lost, survived],
    }, [row("s1", "", { listing: null }), row("s2", "Canon AE-1")]);
    expect(titles()).toEqual(["(untitled)", "Canon AE-1"]);
    await act(async () => { root.unmount(); });
  });

  it("reads as the failure it is when the server says so", async () => {
    // What the server sends now: an item neither disk nor the database could
    // produce is not a draft, and it gets a card of its own saying what the
    // seller can do — it has no stored listing for the drafts grid to show.
    const reason = "This draft couldn't be recovered after the server "
      + "restarted. The rest of the batch is unaffected — re-upload this "
      + "item's photos to draft it again.";
    const lost = { session_id: "s1", status: "error", title: "item 1",
                   listing: null, error: reason };
    const { root, text } = await mount(
      finished([lost, survived]), [row("s2", "Canon AE-1")]);
    expect(text()).toContain(reason);
    // Not a draft: no card in the grid, and not one of the drafts "Publish
    // all" would post, because there is no listing behind it.
    expect(titles()).toEqual(["Canon AE-1"]);
    expect(text()).toContain("Publish all (1)");
    await act(async () => { root.unmount(); });
  });
});

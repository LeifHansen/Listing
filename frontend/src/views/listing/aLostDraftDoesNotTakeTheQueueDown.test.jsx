/* One item the server couldn't rebuild must not blank the whole batch.
 *
 * A batch interrupted by a restart is picked back up and its finished items
 * are rebuilt from disk (main._bulk_items_from_disk). An item whose
 * listing.json did not survive came back as `listing: null` with its status
 * still "draft" — and the queue's render reads every draft's blockers
 * straight off its listing. `ebayBlockers(null)` threw, and because that call
 * runs in the QUEUE's render rather than a card's, the throw unmounted the
 * whole tree: the seller lost a screen full of perfectly good drafts to the
 * error boundary and saw "This screen ran into a problem".
 *
 * The server no longer sends that shape (a listing neither disk nor the
 * database can produce goes out as the failure it is), but the queue must
 * survive it regardless — a batch screen is not something to lose over one
 * item, and the shape can still arrive from a client mid-upgrade.
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

function server(status) {
  return (url) => {
    const path = String(url);
    if (path.startsWith("/api/bulk/status/")) return json(status);
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true },
                    listings: [] });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" });
  };
}

async function mount(status) {
  vi.stubGlobal("fetch", vi.fn(server(status)));
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

// Card titles live in an <input>, which textContent does not carry.
function titles() {
  return [...document.body.querySelectorAll('input[aria-label="Title"]')]
    .map((el) => el.value);
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
    // listing at all, sitting beside one that came back fine.
    const lost = { session_id: "s1", status: "draft", title: "item 1",
                   listing: null };
    const { root } = await mount(finished([lost, survived]));
    // Rendering at all is the assertion — before the fix this threw out of
    // act() and never got here. Both cards are on screen: the one with no
    // listing behind it (falling back to the name the grouping gave it) and
    // the one that came back whole.
    expect(titles()).toEqual(["item 1", "Canon AE-1"]);
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
    });
    expect(titles()).toEqual(["item 1", "Canon AE-1"]);
    await act(async () => { root.unmount(); });
  });

  it("reads as the failure it is when the server says so", async () => {
    // What the server sends now: an item neither disk nor the database could
    // produce is not a draft, and its card says what the seller can do.
    const reason = "This draft couldn't be recovered after the server "
      + "restarted. The rest of the batch is unaffected — re-upload this "
      + "item's photos to draft it again.";
    const lost = { session_id: "s1", status: "error", title: "item 1",
                   listing: null, error: reason };
    const { root, text } = await mount(finished([lost, survived]));
    expect(text()).toContain(reason);
    // Not an editable draft — no title box to type into, and it is not one of
    // the drafts Publish all would post, because there is no listing behind it.
    expect(titles()).toEqual(["Canon AE-1"]);
    expect(text()).toContain("Publish all (1)");
    await act(async () => { root.unmount(); });
  });
});

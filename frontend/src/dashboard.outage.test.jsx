/* A store we could not read is not a store with nothing in it.
 *
 * The four tiles across the top of the dashboard -- Active on eBay, Drafts in
 * progress, Sold, Listed today -- are all counted off one page of listings.
 * When that read fails the page is empty, so every tile counts zero and then
 * states it as a fact: "Active on eBay 0 / everything currently live", "Sold
 * $0.00 / nothing in the last 7 days". None of it was measured.
 *
 * This is the same mistake the notification bell made when a failed read
 * rendered as "nothing sold", and it is worse here because a seller reads
 * these numbers to decide what to do next: nothing live is a reason to go
 * list something, and nothing sold in a week is a reason to cut prices. Both
 * conclusions, on a morning when the database is simply slow.
 *
 * The listings area one card below already gets this right -- it says "we
 * couldn't load your listings, this doesn't mean you don't have any". The
 * tiles above it were still saying zero at the same moment, on the same
 * screen, from the same failed read.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { Dashboard } from "@/views/Dashboard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "lahey@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: false },
  "/api/ebay/status": { connected: false },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
};

function json(body, status = 200) {
  return Promise.resolve({
    ok: status < 400,
    status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

/** Every route answers normally except the store, which is `listings`. */
function server(listings) {
  return (url) => {
    const path = String(url);
    if (path.startsWith("/api/listings")) return listings();
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" }, 404);
  };
}

async function mount() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <Dashboard />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, text: () => host.textContent || "" };
}

describe("the dashboard tiles when the store cannot be read", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("says it could not check, instead of counting zero", async () => {
    vi.stubGlobal("fetch", vi.fn(server(() =>
      json({ detail: "We couldn't load your listings just now." }, 503))));
    const { root, text } = await mount();

    // The sub-lines are the assertions that matter: each one is a claim about
    // the seller's account that the failed read cannot support.
    expect(text()).not.toContain("everything currently live");
    expect(text()).not.toContain("nothing in the last 7 days");
    expect(text()).not.toContain("open one to finish & publish");
    expect(text()).toContain("we couldn’t check");
    await act(async () => { root.unmount(); });
  });

  it("still counts a store that really is empty", async () => {
    // The other half: an outage must not be invented either. A seller with no
    // listings has genuinely nothing live, and a dash where a zero belongs
    // would be its own lie.
    vi.stubGlobal("fetch", vi.fn(server(() => json({
      authed: true, db: { configured: true, connected: true }, listings: [],
    }))));
    const { root, text } = await mount();

    expect(text()).toContain("everything currently live");
    expect(text()).not.toContain("we couldn’t check");
    await act(async () => { root.unmount(); });
  });

  it("still counts a store that has listings in it", async () => {
    vi.stubGlobal("fetch", vi.fn(server(() => json({
      authed: true, db: { configured: true, connected: true },
      listings: [
        { id: "l1", status: "published", listing: { title: "A lamp", price: 20 } },
        { id: "l2", status: "draft", listing: { title: "A chair" } },
      ],
    }))));
    const { root, text } = await mount();

    expect(text()).not.toContain("we couldn’t check");
    await act(async () => { root.unmount(); });
  });
});

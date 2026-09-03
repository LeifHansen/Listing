/* A slower, older answer to /api/listings must not overwrite a newer one.
 *
 * Several writers refetch in quick succession: a publish patches the card
 * and refetches, the bulk queue refetches after every listing it publishes,
 * the shell's heartbeat refetches when a batch ends, a focus refreshes a
 * stale copy. Answers do not arrive in the order they were asked, and an
 * EARLIER page that still said "draft" landing after a later one that said
 * "published" put a draft the seller had just published back under Drafts —
 * until a hard refresh asked once, alone. Only the newest request's answer
 * lands now.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: false },
  "/api/ebay/status": { connected: false },
  "/api/notifications": { notifications: [], unread: 0 },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
};

function reply(body) {
  return {
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}

function page(status) {
  return { authed: true, db: { configured: true, connected: true },
           listings: [{ id: "s1", title: "Levi's 501", status }] };
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

describe("the listings cache", () => {
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("keeps the newest answer when an older one arrives late", async () => {
    // The boot load answers "draft" at once. The next two loads are the
    // ones under test: the first is held back and answers "draft" late, the
    // second answers "published" straight away.
    let held = null;
    let listingsCalls = 0;
    vi.stubGlobal("fetch", vi.fn((url) => {
      const path = String(url);
      if (path.startsWith("/api/listings")) {
        listingsCalls += 1;
        if (listingsCalls === 2) {
          return new Promise((resolve) => { held = () => resolve(reply(page("draft"))); });
        }
        return Promise.resolve(reply(page(listingsCalls >= 3 ? "published" : "draft")));
      }
      const key = Object.keys(BASE).find((k) => path.startsWith(k));
      return Promise.resolve(reply(key ? BASE[key] : { detail: "Not found" }));
    }));
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    let app = null;
    await act(async () => {
      root.render(
        <ToastProvider><AppProvider><Probe onValue={(a) => { app = a; }} /></AppProvider></ToastProvider>,
      );
    });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(app.listingsState.items[0].status).toBe("draft");

    // The publish: patch, then two refetches in flight at once.
    let slow;
    await act(async () => {
      app.patchListing("s1", { status: "published" });
      slow = app.loadListings({ quiet: true });          // answers late, "draft"
      await app.loadListings({ quiet: true });           // answers now, "published"
    });
    expect(app.listingsState.items[0].status).toBe("published");

    // Now the old answer lands. It is superseded and changes nothing.
    await act(async () => { held(); await slow; });
    expect(app.listingsState.items[0].status).toBe("published");
    await act(async () => { root.unmount(); });
  });
});

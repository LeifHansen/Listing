/* The listing that opens is the one tapped last.
 *
 * Opening a listing is a read, and for an imported one a photo copy of up to
 * 24 files, so its answer can take a while. Tapping one listing and then
 * another could land the FIRST answer second: it replaced the listing the
 * seller had already moved on to, and any edits made there in between went
 * with it. Same newest-wins guard as loadListings and loadMetrics.
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

function answer(body) {
  return {
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}

const record = (id, title) => ({ id, status: "draft", listing: { title, images: [] } });

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

let root;
let host;

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  vi.unstubAllGlobals();
});

describe("opening a listing", () => {
  it("lands the listing tapped last, whichever answer is slower", async () => {
    let releaseA;
    const slowA = new Promise((r) => { releaseA = r; });
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const path = String(url);
      if (path === "/api/listings/a") {
        await slowA;
        return answer(record("a", "First tapped"));
      }
      if (path === "/api/listings/b") return answer(record("b", "Tapped last"));
      if (path.startsWith("/api/listings")) {
        return answer({ authed: true, db: { configured: true, connected: true },
                        listings: [] });
      }
      const key = Object.keys(BASE).find((k) => path.startsWith(k));
      return answer(key ? BASE[key] : {});
    }));
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    let app = null;
    await act(async () => {
      root.render(<ToastProvider><AppProvider>
        <Probe onValue={(a) => { app = a; }} /></AppProvider></ToastProvider>);
    });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

    let first;
    await act(async () => { first = app.openListing("a"); });
    await act(async () => { await app.openListing("b"); });
    expect(app.session?.sessionId).toBe("b");
    releaseA();
    await act(async () => { await first; });
    expect(app.session?.sessionId).toBe("b");
    expect(app.session?.listing?.title).toBe("Tapped last");
  });
});

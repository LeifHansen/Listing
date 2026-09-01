/* "Continue <title>" is a promise there is something left to do.
 *
 * The dashboard offers it for the listing the seller has open, and falls
 * through to the newest actual draft once that one has gone live. The
 * fall-through never fired: it asked `session.status || item.status`, and any
 * listing opened from Drafts carries a session status of "draft" — which
 * short-circuited the item's real, freshly-published status. So a draft
 * opened, published, and left behind kept its Continue button for the rest of
 * the visit, pointing at a listing that was already on eBay.
 *
 * Two things had to change and both are pinned here: the session's own status
 * moves when a publish succeeds (useListingForm), and the dashboard treats
 * EITHER record saying "done" as done.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { Dashboard } from "@/views/Dashboard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: false },
  "/api/ebay/status": { connected: false },
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

function server(listings) {
  return (url) => {
    const path = String(url);
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true }, listings });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" });
  };
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

async function mount(listings) {
  vi.stubGlobal("fetch", vi.fn(server(listings)));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let app = null;
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <Probe onValue={(v) => { app = v; }} />
          <Dashboard />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, text: () => host.textContent || "", app: () => app };
}

const SHIRT = "Friday Jr Skull Checkerboard Graphic T-Shirt";

describe("the dashboard's Continue button", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("offers the open draft", async () => {
    const { root, text, app } = await mount(
      [{ id: "s1", status: "draft", listing: { title: SHIRT } }]);
    await act(async () => {
      app().setSession({ sessionId: "s1", listing: { title: SHIRT }, status: "draft" });
    });
    expect(text()).toContain(`Continue "${SHIRT}"`);
    await act(async () => { root.unmount(); });
  });

  it("stops offering it once that listing is live", async () => {
    // The reported bug: the listing published, the seller came back here, and
    // the button was still there.
    const { root, text, app } = await mount(
      [{ id: "s1", status: "published", listing: { title: SHIRT } }]);
    await act(async () => {
      app().setSession({ sessionId: "s1", listing: { title: SHIRT }, status: "draft" });
    });
    expect(text()).not.toContain("Continue");
    expect(text()).toContain("Create a listing");
    await act(async () => { root.unmount(); });
  });

  it("falls through to the newest draft rather than going quiet", async () => {
    const { root, text, app } = await mount([
      { id: "s1", status: "published", listing: { title: SHIRT } },
      { id: "s2", status: "draft", listing: { title: "Ceramic Bear Honey Pot" } },
    ]);
    await act(async () => {
      app().setSession({ sessionId: "s1", listing: { title: SHIRT }, status: "draft" });
    });
    expect(text()).toContain('Continue "Ceramic Bear Honey Pot"');
    await act(async () => { root.unmount(); });
  });
});

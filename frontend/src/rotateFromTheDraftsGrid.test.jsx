/* A sideways photo on the drafts grid is fixed on the drafts grid.
 *
 * The rotate the editor's photo strip has was the only rotate there was, so
 * a seller who could see from the grid that a photo was on its side had to
 * open the draft, turn it there, and come back. The card's rotate button
 * makes the same server call the editor makes, for that listing's own main
 * photo, and the grid re-reads the store afterwards so every card showing
 * the listing catches up.
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

function json(body, status = 200) {
  return Promise.resolve({
    ok: status < 400, status, statusText: status < 400 ? "OK" : "Bad Request",
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

const DRAFT = {
  id: "d1",
  status: "draft",
  updated_at: "2026-03-09T00:00:00Z",
  listing: {
    title: "Brass desk lamp", price: 24.99, quantity: 1,
    condition: "USED_EXCELLENT", category_id: "11450", images: ["a.jpg"],
    package_weight_lb: 1, package_weight_oz: 0,
  },
};

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

let host;
let root;
let app;
let fetchMock;

/** Mounts the grid over one draft. `rotate` answers /api/rotate-image. */
async function mount({ rotate = () => json({ ok: true, version: 1756800000123 }) } = {}) {
  fetchMock = vi.fn((url, opts) => {
    const path = String(url);
    if (path.startsWith("/api/rotate-image")) return rotate(opts);
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true },
                    listings: [DRAFT] });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({});
  });
  vi.stubGlobal("fetch", fetchMock);
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider>
        <Probe onValue={(v) => { app = v; }} />
        <DraftsStrip />
      </AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const rotateButton = () =>
  host.querySelector('button[aria-label="Rotate photo 90°"]');
const photoBox = () => host.querySelector("img").parentElement;
const listingsReads = () =>
  fetchMock.mock.calls.filter(([url]) => String(url) === "/api/listings").length;

async function tap(el) {
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

describe("rotating a photo from the drafts grid", () => {
  it("turns the draft's main photo on the server, then re-reads the store",
    async () => {
      await mount();
      expect(rotateButton(), "no rotate button on the draft card").toBeTruthy();
      const readsBefore = listingsReads();

      await tap(rotateButton());

      const call = fetchMock.mock.calls.find(([url]) =>
        String(url) === "/api/rotate-image");
      expect(call, "the rotate route was never asked").toBeTruthy();
      expect(call[1].method).toBe("POST");
      expect(JSON.parse(call[1].body)).toEqual({ session_id: "d1", name: "a.jpg" });

      // The card asks for the rotated file by its own version straight away
      // — the store's refetch (coalesced, so it waits a beat) is what brings
      // the bumped updated_at to every other card showing this listing.
      expect(host.querySelector("img").src).toContain("?v=1756800000123");
      await act(async () => { await new Promise((r) => setTimeout(r, 450)); });
      expect(listingsReads()).toBeGreaterThan(readsBefore);
    });

  it("says so, and puts the photo back, when the server refused", async () => {
    await mount({ rotate: () => json({ detail: "Couldn't rotate that photo." }, 400) });

    await tap(rotateButton());

    expect(document.body.textContent).toContain("Couldn't rotate");
    // The saved file did not turn, so neither does the card.
    expect(photoBox().style.transform).toBe("");
    // And no refetch was owed: nothing changed.
    expect(host.querySelector("img").src).not.toContain("?v=1756800000123");
  });

  it("is the store's own action, so every screen wires the same one", async () => {
    await mount();
    expect(typeof app.rotateListingPhoto).toBe("function");
  });
});

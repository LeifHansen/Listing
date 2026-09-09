/* How sure the AI was belongs on the face of a draft card.
 *
 * The identify pass grades its own draft — low, medium, high — and the editor
 * showed the grade in its header for as long as that session stayed open.
 * Then the draft was saved and the grade was not, so every card a seller
 * actually triages from (the dashboard, the listings manager, the drafts
 * strip, the bulk queue) showed forty drafts that all looked equally sure.
 * The server now stamps it onto the draft itself (listing.ai_confidence);
 * this pins that the cards read it, and that they read it only on drafts.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { ConfidenceChip } from "@/components/ui/badges";
import { Dashboard } from "@/views/Dashboard";
import { ListingsView } from "@/views/ListingsView";

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

async function mount(node) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let app = null;
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <Probe onValue={(v) => { app = v; }} />
          {node}
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, host, app: () => app };
}

async function mountScreen(Screen, listings) {
  vi.stubGlobal("fetch", vi.fn(server(listings)));
  return mount(<Screen />);
}

const record = (id, status, listing) => ({
  id, status, updated_at: "2026-09-01T00:00:00Z",
  listing: { title: `Item ${id}`, price: 24.99, category_suggestion: "Fish", ...listing },
});

// The chip itself: its label, and the tooltip that says what the label means.
const chips = (host) => [...host.querySelectorAll("[title^='AI confidence:']")];
const GRADE = /AI: (low|medium|high)/;

describe("the AI's confidence on a draft card", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("shows on the dashboard's recent cards, with what it means", async () => {
    const { root, host } = await mountScreen(Dashboard,
      [record("d1", "draft", { ai_confidence: "low" })]);
    expect(host.textContent).toContain("AI: low");
    const [chip] = chips(host);
    expect(chip).toBeTruthy();
    // The word alone reads as a grade on the draft; the tooltip says which
    // fields the grade is about.
    expect(chip.title).toMatch(/^AI confidence: low — /);
    expect(chip.title).toMatch(/title, brand and price/);
    await act(async () => { root.unmount(); });
  });

  it("shows on the listings manager's cards too", async () => {
    // Drafts appear there under "All" — the Active tab is live listings only.
    const { root, host, app } = await mountScreen(ListingsView,
      [record("d1", "draft", { ai_confidence: "medium" })]);
    await act(async () => { app().setListingsTab("all"); });
    expect(host.textContent).toContain("AI: medium");
    expect(chips(host).length).toBeGreaterThan(0);
    await act(async () => { root.unmount(); });
  });

  it("is each draft's own, not the batch's", async () => {
    const { root, host } = await mountScreen(Dashboard, [
      record("d1", "draft", { ai_confidence: "high" }),
      record("d2", "draft", { ai_confidence: "low" }),
    ]);
    expect(chips(host).map((c) => c.textContent.trim()).sort())
      .toEqual(["AI: high", "AI: low"]);
    await act(async () => { root.unmount(); });
  });

  it("says nothing for a listing the AI never drafted", async () => {
    // An import, a relist, a hand-made listing: no verdict is not "medium".
    const { root, host } = await mountScreen(Dashboard,
      [record("d1", "draft", {}), record("d2", "draft", { ai_confidence: "" })]);
    expect(host.textContent).toContain("Item d1");
    expect(host.textContent).not.toMatch(GRADE);
    expect(chips(host)).toEqual([]);
    await act(async () => { root.unmount(); });
  });

  it("stays off a live listing, which the seller has stood behind", async () => {
    // The record still carries the field — a live listing was once a draft —
    // but the AI's doubts about the first draft are not a fact about the
    // listing that is selling.
    const { root, host, app } = await mountScreen(ListingsView,
      [record("l1", "published", { ai_confidence: "low" })]);
    await act(async () => { app().setListingsTab("all"); });
    expect(host.textContent).toContain("Item l1");
    expect(host.textContent).not.toMatch(GRADE);
    await act(async () => { root.unmount(); });
  });

  it("draws nothing for a grade it does not know", async () => {
    // The server already refuses anything but the three levels; the chip
    // refuses too, because it is the thing rendering text into the DOM.
    vi.stubGlobal("fetch", vi.fn(server([])));
    const { root, host } = await mount(
      <>
        <ConfidenceChip level="very sure" />
        <ConfidenceChip level="" />
        <ConfidenceChip level={undefined} />
      </>,
    );
    expect(chips(host)).toEqual([]);
    expect(host.textContent).not.toContain("AI:");
    await act(async () => { root.unmount(); });
  });
});

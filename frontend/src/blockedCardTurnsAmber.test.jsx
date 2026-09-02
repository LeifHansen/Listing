/* A listing that can't be posted yet says so with the whole card.
 *
 * The warning was there — a line of amber text under the card naming the
 * fields, and, for a publish eBay actually refused, a toast. On a grid of
 * twenty drafts neither reads: the line is one small row among many and the
 * toast is gone in seconds, after which the card looks exactly like one that
 * is ready to go. Sellers published straight past both.
 *
 * So the card itself carries the state: amber background, amber edge, and a
 * "needs info" chip so the state is never colour alone. Two ways in, and they
 * have to look the same because they mean the same thing — a field a browser
 * can already see eBay will refuse the listing over, and a publish eBay
 * turned down. The one thing that must NOT turn a card amber is a publish
 * nobody could get an answer to: the listing may well be live, and the next
 * step there is to check the store, never to edit and republish.
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

// Everything eBay demands of a new listing (see blockers.js), so nothing
// local is holding this one back.
function ready(id, title) {
  return {
    id,
    status: "draft",
    updated_at: "2026-03-09T00:00:00Z",
    listing: {
      title, price: 24.5, quantity: 1, condition: "USED_EXCELLENT",
      category_id: "11450", images: ["a.jpg"],
      package_weight_lb: 1, package_weight_oz: 0,
    },
  };
}

// The same listing with no price — a blocker a browser can see by itself.
function blocked(id, title) {
  const item = ready(id, title);
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
      return json(state.publish(id));
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

async function mount(listings, publish = () => ({ published: true })) {
  const state = { listings, publish };
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
  // The card is the one button carrying the card shell's own classes.
  const cards = () => [...host.querySelectorAll("button")]
    .filter((b) => b.className.includes("rounded-card"));
  return {
    root,
    state,
    app: () => app,
    text: () => host.textContent || "",
    cards,
    amber: () => cards().map((b) => b.className.includes("bg-warning-soft")),
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

describe("a listing that needs updating before it can be posted", () => {
  beforeEach(() => { localStorage.clear(); vi.useFakeTimers(); });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("paints the card amber when a field eBay requires is missing", async () => {
    const ui = await mount([blocked("d1", "Flamingo Camp Shirt")]);

    expect(ui.amber()).toEqual([true]);
    expect(ui.text()).toContain("needs info");
    expect(ui.text()).toContain("Keeping this off eBay");

    await act(async () => { ui.root.unmount(); });
  });

  it("leaves a listing that is ready to go alone", async () => {
    const ui = await mount([ready("d1", "Enamel Coffee Pot")]);

    expect(ui.amber()).toEqual([false]);
    expect(ui.text()).not.toContain("needs info");

    await act(async () => { ui.root.unmount(); });
  });

  it("paints the card amber when eBay refuses the publish", async () => {
    // Nothing local is blocking this one — the refusal is the only thing
    // that could ever tell the seller, and until now it only said it in a
    // toast that is gone in seconds.
    const ui = await mount(
      [ready("d1", "Enamel Coffee Pot")],
      () => ({
        published: false,
        issues: [{ level: "error", target: "title",
                   title: "eBay needs a shorter title." }],
      }),
    );
    expect(ui.amber()).toEqual([false]);

    await publishCard(ui);

    expect(ui.amber()).toEqual([true]);
    // And the reason stays ON the card, where the toast used to be the only
    // copy of it.
    expect(ui.text()).toContain("eBay needs a shorter title.");

    await act(async () => { ui.root.unmount(); });
  });

  it("does not paint a publish nobody could get an answer to", async () => {
    // outcome_unknown is not a refusal: the listing may be live on eBay right
    // now, and an amber "needs info" card is an invitation to edit and
    // republish it — the one thing that must not happen.
    const ui = await mount(
      [ready("d1", "Enamel Coffee Pot")],
      () => ({ published: false, outcome_unknown: true }),
    );

    await publishCard(ui);

    expect(ui.amber()).toEqual([false]);

    await act(async () => { ui.root.unmount(); });
  });

  it("clears last time's refusal when the seller tries again", async () => {
    let refuse = true;
    const ui = await mount(
      [ready("d1", "Enamel Coffee Pot")],
      (id) => (refuse
        ? { published: false,
            issues: [{ level: "error", target: "title",
                       title: "eBay needs a shorter title." }] }
        : { published: true, listing_id: "110123456789" }),
    );

    await publishCard(ui);
    expect(ui.amber()).toEqual([true]);

    refuse = false;
    ui.state.listings = [ready("d1", "Enamel Coffee Pot")];
    await publishCard(ui);

    expect(ui.text()).not.toContain("eBay needs a shorter title.");

    await act(async () => { ui.root.unmount(); });
  });
});

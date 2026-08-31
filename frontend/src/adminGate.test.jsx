/* The console renders only for a superadmin — and its absence is silent.
 *
 * The role gate that matters lives on the server (every /api/admin route
 * re-checks it and 404s), but the CLIENT decides what to render, and two
 * mistakes are possible here: showing the Admin entry to an ordinary seller
 * (an invitation to a surface that will 404 them), or hiding it from the
 * operator (a console nobody can reach). Both are one boolean read off
 * /api/auth/me, so both are pinned.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/health": { anthropic_configured: true, ebay_configured: false },
  "/api/ebay/status": { connected: false },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
  "/api/listings": {
    authed: true, db: { configured: true, connected: true }, listings: [],
  },
  "/api/admin/overview": {
    available: true, days: 30,
    users: { total: 1, signups: 1, signup_series: [], active: 1 },
    listings: { by_status: {}, total: 0 },
    sales: { count: 0, value: 0, approx: 0, undated: 0,
             mixed_currency: false, currency: null },
    tokens: { by_kind: {}, features: [] },
    deletion_backlog: { media_purges: 0, deletion_notices: 0 },
    owed_refunds: 0,
  },
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

function server(me) {
  return (url) => {
    const path = String(url);
    if (path.startsWith("/api/auth/me")) return json({ user: me });
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" }, 404);
  };
}

async function mount() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(<App />); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, host, text: () => host.textContent || "" };
}

function navButton(host, label) {
  return [...host.querySelectorAll("nav button")]
    .find((b) => (b.textContent || "").trim() === label);
}

describe("the admin console gate", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("never offers the Admin entry to an ordinary seller", async () => {
    vi.stubGlobal("fetch", vi.fn(server(
      { id: "u1", email: "seller@example.com", role: "user" })));
    const { root, host } = await mount();

    expect(navButton(host, "Admin")).toBeUndefined();
    await act(async () => { root.unmount(); });
  });

  it("nor to a signed-out visitor", async () => {
    vi.stubGlobal("fetch", vi.fn(server(null)));
    const { root, host } = await mount();

    expect(navButton(host, "Admin")).toBeUndefined();
    await act(async () => { root.unmount(); });
  });

  it("offers it to a superadmin, and the entry opens the console", async () => {
    vi.stubGlobal("fetch", vi.fn(server(
      { id: "u1", email: "op@example.com", role: "superadmin" })));
    const { root, host, text } = await mount();

    const entry = navButton(host, "Admin");
    expect(entry).toBeDefined();

    await act(async () => { entry.click(); });
    // AnimatePresence mode="wait" plays the old view's exit animation before
    // mounting the console, so give the transition real time to finish.
    for (let i = 0; i < 10 && !text().includes("Overview"); i++) {
      await act(async () => { await new Promise((r) => setTimeout(r, 100)); });
    }

    expect(entry.getAttribute("aria-current")).toBe("page");
    expect(text()).toContain("Overview");
    expect(text()).toContain("Accounts");
    await act(async () => { root.unmount(); });
  });
});

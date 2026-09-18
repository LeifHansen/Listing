/* "The AI isn't configured" has two meanings now, and they need two sentences.
 *
 * Identification runs on Google's Gemini where GOOGLE_API_KEY is set, and on
 * Claude otherwise; refine and the item-specifics fills are Claude's either
 * way. The banner used to read one bit — `anthropic_configured` — and say
 * photo identification was broken whenever it was false. On a server with
 * only a Google key that is a lie twice over: identification works fine, and
 * the operator is sent to add a key they do not need.
 *
 * So: no banner when either backend can identify and the other capability is
 * covered, the RIGHT sentence when only one of the two keys is there, and
 * nothing at all before /api/health has landed (a banner rendered against
 * defaults accuses a server nobody has heard from yet).
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: "u1", email: "seller@example.com" } },
  "/api/ebay/status": { connected: false },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
  "/api/listings": {
    authed: true, db: { configured: true, connected: true }, listings: [],
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

/** Mount the app against a server whose /api/health says `health`. */
async function mount(health) {
  const routes = { ...BASE, "/api/health": health };
  vi.stubGlobal("fetch", vi.fn((url) => {
    const path = String(url);
    const key = Object.keys(routes).find((k) => path.startsWith(k));
    return key ? json(routes[key]) : json({ detail: "Not found" }, 404);
  }));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(<App />); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, text: () => host.textContent || "" };
}

describe("the setup banner", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("says nothing when both backends are configured", async () => {
    const { root, text } = await mount({
      anthropic_configured: true, google_ai_configured: true,
      identify_provider: "google", ebay_configured: false,
    });
    expect(text()).not.toContain("isn't configured");
    expect(text()).not.toContain("ANTHROPIC_API_KEY");
    await act(async () => { root.unmount(); });
  });

  it("does not accuse a Google-only server of being unable to identify", async () => {
    // The bug this test exists for. Identification is working.
    const { root, text } = await mount({
      anthropic_configured: false, google_ai_configured: true,
      identify_provider: "google", ebay_configured: false,
    });
    expect(text()).not.toContain("photo identification won't work");
    // It still says the true thing: refine needs the other key.
    expect(text()).toContain("running on Google");
    expect(text()).toContain("ANTHROPIC_API_KEY");
    await act(async () => { root.unmount(); });
  });

  it("says nothing about Google on a Claude-only server", async () => {
    const { root, text } = await mount({
      anthropic_configured: true, google_ai_configured: false,
      identify_provider: "anthropic", ebay_configured: false,
    });
    expect(text()).not.toContain("isn't configured");
    expect(text()).not.toContain("GOOGLE_API_KEY");
    await act(async () => { root.unmount(); });
  });

  it("names both keys when neither is set", async () => {
    const { root, text } = await mount({
      anthropic_configured: false, google_ai_configured: false,
      identify_provider: "anthropic", ebay_configured: false,
    });
    expect(text()).toContain("GOOGLE_API_KEY");
    expect(text()).toContain("ANTHROPIC_API_KEY");
    expect(text()).toContain("photo identification won't work");
    await act(async () => { root.unmount(); });
  });
});

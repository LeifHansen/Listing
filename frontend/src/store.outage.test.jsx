/* An outage must not log the seller out of the app.
 *
 * The server half of this is fixed: a session lookup that cannot run now
 * answers 503 instead of pretending nobody is there. That only helps if the
 * client reads the difference — and `loadAuth` cleared `user` on ANY error,
 * so the honest 503 landed as "not signed in" and took the whole session with
 * it. Every gated surface hangs off `user`: the listings, the bell, the eBay
 * state, the token balance. A seller mid-session watched their store turn
 * into the logged-out pitch because Postgres hiccuped for one poll.
 *
 * Only a definitive answer clears the session now: a 4xx. A 5xx or a dropped
 * connection is the server failing to answer, and the right response to that
 * is to keep what we already know. On a cold load there is nothing to keep,
 * so the login screen is still what a first-time visitor sees.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const SIGNED_IN = {
  "/api/auth/me": { user: { id: 7, email: "lahey@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: false },
  "/api/listings": {
    authed: true,
    db: { configured: true, connected: true },
    listings: [{ id: "l1", title: "A trailer park lamp", status: "active" }],
  },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: true, total: 250, packs: [], costs: {} },
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

function respond(path) {
  const key = Object.keys(SIGNED_IN).find((k) => path.startsWith(k));
  return key ? json(SIGNED_IN[key]) : json({ detail: "Not found" }, 404);
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

async function mountSignedIn() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  let app = null;
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <Probe onValue={(v) => { app = v; }} />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, host, get: () => app };
}

describe("a database outage mid-session", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn((url) => respond(String(url))));
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("keeps the seller signed in when /api/auth/me answers 503", async () => {
    const { get, root } = await mountSignedIn();
    expect(get().user.email).toBe("lahey@example.com");

    fetch.mockImplementation((url) => (
      String(url).startsWith("/api/auth/me")
        ? json({ detail: "We couldn’t verify your session just now." }, 503)
        : respond(String(url))));
    await act(async () => { await get().loadAuth(); });

    expect(get().user).not.toBe(null);
    expect(get().user.email).toBe("lahey@example.com");
    await act(async () => { root.unmount(); });
  });

  it("keeps the seller signed in when the request never lands", async () => {
    const { get, root } = await mountSignedIn();
    fetch.mockImplementation((url) => (
      String(url).startsWith("/api/auth/me")
        ? Promise.reject(new TypeError("Failed to fetch"))
        : respond(String(url))));
    await act(async () => { await get().loadAuth(); });

    expect(get().user?.email).toBe("lahey@example.com");
    await act(async () => { root.unmount(); });
  });

  it("still signs them out when the server actually says so", async () => {
    const { get, root } = await mountSignedIn();
    fetch.mockImplementation((url) => (
      String(url).startsWith("/api/auth/me")
        ? json({ detail: "Not authenticated" }, 401)
        : respond(String(url))));
    await act(async () => { await get().loadAuth(); });

    expect(get().user).toBe(null);
    await act(async () => { root.unmount(); });
  });

  it("still signs them out when the session is simply over", async () => {
    /* The ordinary expiry path: 200 with a null user. */
    const { get, root } = await mountSignedIn();
    fetch.mockImplementation((url) => (
      String(url).startsWith("/api/auth/me")
        ? json({ user: null })
        : respond(String(url))));
    await act(async () => { await get().loadAuth(); });

    expect(get().user).toBe(null);
    await act(async () => { root.unmount(); });
  });
});

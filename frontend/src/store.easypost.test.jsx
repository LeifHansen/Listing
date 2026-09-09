/* The EasyPost connection is loaded beside the eBay status, and treated the
 * same way: a fetch that fails keeps the previous value rather than reading
 * as "disconnected", and logging out puts it back to the signed-out shape so
 * the next person on this tab is not offered someone else's account.
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
  "/api/ebay/status": { connected: true, env: "production", username: "mr._lahey" },
  "/api/easypost/status": { connected: true, test: true, key_hint: "k9z2" },
  "/api/listings": { authed: true, db: { configured: true, connected: true }, listings: [] },
  "/api/notifications": { notifications: [], unread: 0 },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
  "/api/auth/logout": { ok: true },
};

function respond(path, table = SIGNED_IN) {
  const key = Object.keys(table).find((k) => path.startsWith(k));
  const body = key ? table[key] : null;
  return Promise.resolve({
    ok: !!body,
    status: body ? 200 : 404,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body || { detail: "Not found" }),
    text: () => Promise.resolve(JSON.stringify(body || { detail: "Not found" })),
  });
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

async function mount() {
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
  return { root, get: () => app };
}

describe("the EasyPost connection in the store", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("is asked for at boot and read as the seller's", async () => {
    vi.stubGlobal("fetch", vi.fn((url) => respond(String(url))));
    const { get, root } = await mount();
    expect(get().easypost).toEqual({ connected: true, test: true, key_hint: "k9z2", loaded: true });
    expect(fetch.mock.calls.some(([u]) => String(u).startsWith("/api/easypost/status"))).toBe(true);
    await act(async () => { root.unmount(); });
  });

  it("keeps the signed-out shape when the fetch fails, never 'disconnected'", async () => {
    const { "/api/easypost/status": _gone, ...without } = SIGNED_IN;
    vi.stubGlobal("fetch", vi.fn((url) => respond(String(url), without)));
    const { get, root } = await mount();
    expect(get().easypost.connected).toBe(false);
    expect(get().easypost.loaded).toBe(false);
    await act(async () => { root.unmount(); });
  });

  it("goes with the session on logout", async () => {
    vi.stubGlobal("fetch", vi.fn((url) => respond(String(url))));
    const { get, root } = await mount();
    expect(get().easypost.connected).toBe(true);
    fetch.mockImplementation((url) => (
      String(url).startsWith("/api/auth/logout")
        ? respond("/api/auth/logout")
        : new Promise(() => {})));
    await act(async () => { await get().logout(); });
    expect(get().easypost).toEqual({ connected: false, test: false, key_hint: "", loaded: false });
    await act(async () => { root.unmount(); });
  });
});

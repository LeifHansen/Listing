/* Getting out of a half-linked marketplace connection.
 *
 * A connect can leave a token stored with no shop behind it — Etsy accepts
 * the token exchange and then refuses the identity lookup. account_status
 * reports that as connected + needs_reconnect, and the card's job is to say
 * "reconnect to finish linking it".
 *
 * But the button chain offered Reconnect INSTEAD of Disconnect, and
 * needs_reconnect implies connected, so this was the one state a seller
 * could reach and not leave: no Disconnect anywhere, and reconnecting is
 * exactly what wasn't working. The way out has to stay open, especially
 * when the way forward is stuck.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { SettingsView } from "@/views/SettingsView";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const posted = [];

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: false },
  "/api/ebay/status": { connected: false },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/prefs": { prefs: {} },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/profile": { user: { email: "seller@example.com", display_name: "" },
                    ebay: { connected: false } },
  "/api/account/summary": { listings: 0, images: 0 },
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

function etsy(extra) {
  return {
    key: "etsy", label: "Etsy", oauth_ready: true, oauth_missing: [],
    coming_soon: false, coming_soon_note: "",
    access_pending: false, access_pending_note: "",
    access_unverified: false, access_unverified_note: "",
    connected: true, needs_reconnect: false, username: "",
    env: "production", supports: {}, ...extra,
  };
}

function server(marketplaces) {
  return (url, init) => {
    const path = String(url);
    if (path.startsWith("/api/etsy/disconnect")) {
      posted.push(path);
      return json({ ok: true });
    }
    if (path.startsWith("/api/marketplaces")) return json({ marketplaces });
    if (path.startsWith("/api/etsy/settings-options")) {
      return json({ shipping_profiles: [], return_policies: [], readiness_states: [] });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" }, 404);
  };
}

async function mount(marketplaces) {
  vi.stubGlobal("fetch", vi.fn(server(marketplaces)));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><SettingsView /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  const button = (label) => [...document.querySelectorAll("button")]
    .find((b) => (b.textContent || "").includes(label));
  return {
    root,
    disconnect: () => button("Disconnect"),
    reconnect: () => button("Reconnect Etsy"),
    confirm: () => [...document.querySelectorAll("button")]
      .filter((b) => (b.textContent || "").trim() === "Disconnect").pop(),
    click: async (el) => { await act(async () => { el.click(); }); },
  };
}

describe("a connection that needs reconnecting", () => {
  beforeEach(() => { localStorage.clear(); posted.length = 0; });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("offers the way out as well as the way forward", async () => {
    const s = await mount([etsy({ needs_reconnect: true })]);
    expect(s.reconnect()).toBeTruthy();
    expect(s.disconnect()).toBeTruthy();   // the one that used to be missing
    await act(async () => { s.root.unmount(); });
  });

  it("actually disconnects from that state", async () => {
    const s = await mount([etsy({ needs_reconnect: true })]);
    await s.click(s.disconnect());
    await s.click(s.confirm());            // the confirm dialog's own button
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(posted).toContain("/api/etsy/disconnect");
    await act(async () => { s.root.unmount(); });
  });

  it("leaves a healthy connection with Disconnect alone", async () => {
    const s = await mount([etsy({ username: "MyShop" })]);
    expect(s.disconnect()).toBeTruthy();
    expect(s.reconnect()).toBeFalsy();
    await act(async () => { s.root.unmount(); });
  });
});

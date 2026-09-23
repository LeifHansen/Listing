/* The stop on the way out to Etsy.
 *
 * Etsy's app-type wall is enforced on ETSY'S page, after the browser has
 * left this app, and its refusal ("Only the app owner may authorize a seller
 * app") redirects nothing back — so there is no callback to turn into an
 * error message. The app's one chance to be useful is before the redirect.
 *
 * When the roster IS configured the server already knows the answer and the
 * Connect button is held back (access_pending). This covers the other case:
 * the wall is up and nothing here knows who gets past it. The seller is not
 * refused — on a deploy with no roster the person pressing Connect is
 * usually the one account that works, and turning them away would be the
 * worse guess — but they are told first, and they get to back out.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { SettingsView } from "@/views/SettingsView";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const started = [];
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, startConnect: (path) => { started.push(path); return Promise.resolve(); } };
});

const NOTE = "Etsy hasn’t approved this app for other shops yet.";

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
    connected: false, needs_reconnect: false, username: "",
    env: "production", supports: {}, ...extra,
  };
}

function server(marketplaces) {
  return (url) => {
    const path = String(url);
    if (path.startsWith("/api/marketplaces")) return json({ marketplaces });
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
    text: () => document.body.textContent || "",
    connect: () => button("Connect Etsy"),
    proceed: () => button("Continue to Etsy"),
    cancel: () => button("Cancel"),
    click: async (el) => { await act(async () => { el.click(); }); },
  };
}

describe("connecting Etsy when Etsy may refuse", () => {
  beforeEach(() => { localStorage.clear(); started.length = 0; });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("says so on the card, in Etsy's own words, before anyone clicks", async () => {
    const s = await mount([etsy({ access_unverified: true,
                                  access_unverified_note: NOTE })]);
    expect(s.text()).toContain(NOTE);
    // Not the "pending approval" state: that one holds the button back, and
    // this seller may well be the account that works.
    expect(s.connect()).toBeTruthy();
    expect(s.connect().disabled).toBe(false);
    await act(async () => { s.root.unmount(); });
  });

  it("asks before handing the browser to Etsy, and backing out stays put", async () => {
    const s = await mount([etsy({ access_unverified: true,
                                  access_unverified_note: NOTE })]);
    await s.click(s.connect());
    expect(s.proceed()).toBeTruthy();
    expect(s.text()).toContain(NOTE);
    expect(started).toEqual([]);          // nothing navigated on the ask alone

    await s.click(s.cancel());
    expect(started).toEqual([]);
    await act(async () => { s.root.unmount(); });
  });

  it("lets the seller through once they've read it", async () => {
    const s = await mount([etsy({ access_unverified: true,
                                  access_unverified_note: NOTE })]);
    await s.click(s.connect());
    await s.click(s.proceed());
    expect(started).toEqual(["/api/etsy/connect"]);
    await act(async () => { s.root.unmount(); });
  });

  it("does not interrupt a connect the server can vouch for", async () => {
    // Roster configured and this seller on it (or Commercial Access granted):
    // the app knows it will work, so a warning would be a lie told every time.
    const s = await mount([etsy({})]);
    expect(s.text()).not.toContain("may not let you connect yet");
    await s.click(s.connect());
    expect(started).toEqual(["/api/etsy/connect"]);
    await act(async () => { s.root.unmount(); });
  });
});

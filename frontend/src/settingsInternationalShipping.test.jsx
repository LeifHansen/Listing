/* The "Use eBay International Shipping" switch on Settings.
 *
 * Same contract as the Allow offers switch beside it, for the same reasons:
 *
 *   - it renders in the state the account is actually in, off the saved
 *     preference rather than a component default;
 *   - flipping it and pressing Save posts that choice, and posts the OFF
 *     case as a real 0 -- `/api/prefs` merges, so a field dropped instead of
 *     sent as 0 would leave a seller unable to stop selling abroad;
 *   - a prefs read that FAILED shows no switch at all: an off-looking toggle
 *     is a statement that this account is not selling abroad, made on the
 *     strength of having failed to find out;
 *   - what it commits the seller to is said where they decide it. eBay
 *     International Shipping is not "you post abroad" -- the seller posts to
 *     eBay's US hub and eBay carries it from there -- and it does nothing
 *     for a seller eBay has not enrolled.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { SettingsView } from "@/views/SettingsView";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "lahey@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: false },
  "/api/ebay/status": { connected: false },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/profile": { user: { email: "lahey@example.com", display_name: "" },
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

/** Every route answers normally; `prefs` decides what /api/prefs does. */
function server(prefs, posts) {
  return (url, init) => {
    const path = String(url);
    if (path.startsWith("/api/prefs")) {
      if ((init?.method || "GET") === "POST") {
        const body = JSON.parse(init.body);
        posts.push(body);
        return json({ ok: true, prefs: body });
      }
      return prefs();
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" }, 404);
  };
}

async function mount() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <SettingsView />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  const find = (label) => [...host.querySelectorAll("label")]
    .find((l) => (l.textContent || "").includes(label));
  return {
    root,
    host,
    text: () => host.textContent || "",
    abroad: () => find("Use eBay International Shipping on new listings")
      ?.querySelector("input[type=checkbox]"),
    save: () => [...host.querySelectorAll("button")]
      .find((b) => (b.textContent || "").includes("Save defaults")),
    tips: () => [...host.querySelectorAll("[aria-label]")]
      .map((el) => el.getAttribute("aria-label")).join(" | "),
  };
}

describe("the eBay International Shipping switch", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("shows what the account has saved", async () => {
    const posts = [];
    vi.stubGlobal("fetch", vi.fn(server(
      () => json({ prefs: { ebay_international_shipping: 1 } }), posts)));
    const s = await mount();

    expect(s.abroad()).toBeTruthy();
    expect(s.abroad().checked).toBe(true);
    // The terms, where the seller decides. Section explainers on this
    // screen live behind the hover ⓘ, so that is where they are asserted.
    expect(s.tips()).toContain("US shipping hub");
    expect(s.tips()).toContain("enrolled in the program on eBay");
    expect(s.tips()).toContain("already live are left as they are");
    await act(async () => { s.root.unmount(); });
  });

  it("is off for a seller who has never turned it on", async () => {
    const posts = [];
    vi.stubGlobal("fetch", vi.fn(server(() => json({ prefs: {} }), posts)));
    const s = await mount();

    expect(s.abroad().checked).toBe(false);
    await act(async () => { s.root.unmount(); });
  });

  it("saves the choice the seller made", async () => {
    const posts = [];
    vi.stubGlobal("fetch", vi.fn(server(() => json({ prefs: {} }), posts)));
    const s = await mount();

    await act(async () => { s.abroad().click(); });
    await act(async () => { s.save().click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

    expect(posts.at(-1).ebay_international_shipping).toBe(1);
    await act(async () => { s.root.unmount(); });
  });

  it("saves turning it back off as a real no", async () => {
    const posts = [];
    vi.stubGlobal("fetch", vi.fn(server(
      () => json({ prefs: { ebay_international_shipping: 1 } }), posts)));
    const s = await mount();

    await act(async () => { s.abroad().click(); });
    await act(async () => { s.save().click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

    expect(posts.at(-1).ebay_international_shipping).toBe(0);
    await act(async () => { s.root.unmount(); });
  });

  it("does not flip the Allow offers switch beside it", async () => {
    /* Two switches, one Save: turning on abroad must post offers exactly as
       it was. */
    const posts = [];
    vi.stubGlobal("fetch", vi.fn(server(
      () => json({ prefs: { allow_offers: 0 } }), posts)));
    const s = await mount();

    await act(async () => { s.abroad().click(); });
    await act(async () => { s.save().click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

    expect(posts.at(-1).ebay_international_shipping).toBe(1);
    expect(posts.at(-1).allow_offers).toBe(0);
    await act(async () => { s.root.unmount(); });
  });

  it("shows no switch at all when the defaults could not be read", async () => {
    const posts = [];
    vi.stubGlobal("fetch", vi.fn(server(
      () => json({ detail: "We couldn’t load your saved defaults." }, 503),
      posts)));
    const s = await mount();

    expect(s.abroad()).toBeUndefined();
    expect(s.text()).toContain("this isn’t what you have saved");
    await act(async () => { s.root.unmount(); });
  });
});

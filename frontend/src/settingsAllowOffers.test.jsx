/* The "Allow offers" switch on Settings.
 *
 * A toggle is only a toggle if what the seller flips is what gets saved and
 * what comes back. This pins the three things that make it one:
 *
 *   - it renders in the state the account is actually in, off the saved
 *     preference rather than a component default;
 *   - flipping it and pressing Save posts that choice, and posts the OFF
 *     case as a real 0 -- `/api/prefs` merges, so a field dropped instead of
 *     sent as 0 would leave a seller unable to turn offers back off;
 *   - a prefs read that FAILED shows no switch at all. The panel beside it
 *     already refuses to render the app's fallbacks as the seller's saved
 *     settings, and a switch is the worst thing to get wrong that way: an
 *     off-looking toggle is a statement that this account is not taking
 *     offers, made on the strength of having failed to find out.
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
    offers: () => find("Allow offers on new listings")
      ?.querySelector("input[type=checkbox]"),
    save: () => [...host.querySelectorAll("button")]
      .find((b) => (b.textContent || "").includes("Save defaults")),
    tips: () => [...host.querySelectorAll("[aria-label]")]
      .map((el) => el.getAttribute("aria-label")).join(" | "),
  };
}

describe("the Allow offers switch", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("shows what the account has saved", async () => {
    const posts = [];
    vi.stubGlobal("fetch", vi.fn(server(
      () => json({ prefs: { allow_offers: 1 } }), posts)));
    const s = await mount();

    expect(s.offers()).toBeTruthy();
    expect(s.offers().checked).toBe(true);
    // The terms are stated where the seller decides, not left for the first
    // $5 offer on a $200 item to explain. Section explainers on this screen
    // live behind the hover ⓘ, so that is where this is asserted.
    expect(s.tips()).toContain("no minimum");
    expect(s.tips()).toContain("auctions don’t take offers");
    await act(async () => { s.root.unmount(); });
  });

  it("is off for a seller who has never turned it on", async () => {
    const posts = [];
    vi.stubGlobal("fetch", vi.fn(server(() => json({ prefs: {} }), posts)));
    const s = await mount();

    expect(s.offers().checked).toBe(false);
    await act(async () => { s.root.unmount(); });
  });

  it("saves the choice the seller made", async () => {
    const posts = [];
    vi.stubGlobal("fetch", vi.fn(server(() => json({ prefs: {} }), posts)));
    const s = await mount();

    await act(async () => { s.offers().click(); });
    await act(async () => { s.save().click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

    expect(posts.at(-1).allow_offers).toBe(1);
    await act(async () => { s.root.unmount(); });
  });

  it("saves turning it back off as a real no", async () => {
    /* `/api/prefs` MERGES. A toggle that omitted the field when off would
       leave the last "on" standing for ever. */
    const posts = [];
    vi.stubGlobal("fetch", vi.fn(server(
      () => json({ prefs: { allow_offers: 1 } }), posts)));
    const s = await mount();

    await act(async () => { s.offers().click(); });
    await act(async () => { s.save().click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

    expect(posts.at(-1).allow_offers).toBe(0);
    await act(async () => { s.root.unmount(); });
  });

  it("shows no switch at all when the defaults could not be read", async () => {
    const posts = [];
    vi.stubGlobal("fetch", vi.fn(server(
      () => json({ detail: "We couldn’t load your saved defaults." }, 503),
      posts)));
    const s = await mount();

    expect(s.offers()).toBeUndefined();
    expect(s.text()).toContain("this isn’t what you have saved");
    await act(async () => { s.root.unmount(); });
  });
});

/* Tapping a nav item lands on a screen, never on an empty one.
 *
 * A seller reported that clicking Home "brings me to a dead screen": the nav
 * bar still there, the whole area under it blank. Nothing had crashed — the
 * error boundary shows a card when something throws, and there was no card.
 * The main area was simply empty, and stayed empty.
 *
 * Two ways it could be, and both are gone now:
 *
 *   1. THE ANIMATION GATE. The screens rendered inside
 *      `<AnimatePresence mode="wait">`, which holds the incoming screen
 *      UNMOUNTED until the outgoing one has finished animating away. Anything
 *      that stops that exit finishing — the webview backgrounded mid-tap, a
 *      second tap while the first is still running, a frame loop the OS
 *      suspended — leaves nothing mounted at all, and leaves it that way.
 *      The nav bar survives because it lives outside the element, so the app
 *      looks alive and empty. A screen is worth more than the 180ms it fades
 *      in over: it mounts first now and animates second.
 *
 *   2. NO FALLBACK. The main area was a chain of `{view === "x" && <X/>}`
 *      with no final else, so any value nobody had thought of rendered
 *      nothing — same blank page, no crash, no report.
 *
 * The assertions below deliberately do NOT wait: "the screen is there on the
 * next commit" is the property that broke, so a test that retries for a
 * second would pass against the bug.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App, { screenFor } from "@/App";
import { Dashboard } from "@/views/Dashboard";
import { SettingsView } from "@/views/SettingsView";
import { AdminView } from "@/views/AdminView";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: "u1", email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: false },
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

const server = (url) => {
  const path = String(url);
  const key = Object.keys(BASE).find((k) => path.startsWith(k));
  return key ? json(BASE[key]) : json({ detail: "Not found" }, 404);
};

async function mount() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(<App />); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, host, text: () => host.textContent || "" };
}

/** A sidebar/bottom-nav button by its label. */
function navButton(host, label) {
  return [...host.querySelectorAll("nav button")]
    .find((b) => (b.textContent || "").trim() === label);
}

/** Whatever the main area is currently showing. */
const main = (host) => host.querySelector("main");

describe("the screen under the nav bar", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn(server));
  });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("is never empty on the first paint", async () => {
    const { root, host, text } = await mount();

    expect(main(host)).not.toBeNull();
    expect((main(host).textContent || "").trim().length).toBeGreaterThan(0);
    expect(text()).toContain("Ready to flip something today?");
    await act(async () => { root.unmount(); });
  });

  it("is not empty for one frame on the way back to Home", async () => {
    // The seller's report, in the order they hit it: leave Home, come back.
    // No polling loop here — the bug WAS the wait.
    const { root, host, text } = await mount();

    await act(async () => { navButton(host, "Sell").click(); });
    expect((main(host).textContent || "").trim().length).toBeGreaterThan(0);

    await act(async () => { navButton(host, "Home").click(); });
    expect((main(host).textContent || "").trim().length).toBeGreaterThan(0);
    expect(text()).toContain("Ready to flip something today?");
    await act(async () => { root.unmount(); });
  });

  it("survives being tapped twice before the first tap has settled", async () => {
    // Exactly what `mode="wait"` could not do: a second key change while the
    // first exit was still running. On a phone this is one impatient thumb.
    const { root, host, text } = await mount();

    await act(async () => {
      navButton(host, "Sell").click();
      navButton(host, "Home").click();
    });

    expect((main(host).textContent || "").trim().length).toBeGreaterThan(0);
    expect(text()).toContain("Ready to flip something today?");
    await act(async () => { root.unmount(); });
  });

  it("keeps its content while moving between other screens too", async () => {
    const { root, host } = await mount();

    for (const label of ["Shop", "Settings", "Home", "Sell"]) {
      await act(async () => { navButton(host, label).click(); });
      expect((main(host).textContent || "").trim().length).toBeGreaterThan(0);
    }
    await act(async () => { root.unmount(); });
  });
});

/** Which component a view name renders (screenFor hands back the element). */
const screenType = (view, isSuperadmin = false) =>
  screenFor(view, isSuperadmin).type;

describe("the screen a view name maps to", () => {
  it("is Home for anything nobody thought of", () => {
    // A stale value written by an older build, a name that has since been
    // renamed. Neither is a crash, so nothing reports it — the seller just
    // gets a blank page and no way to say what happened.
    expect(screenType("dashboard")).toBe(Dashboard);
    expect(screenType("a-view-that-never-existed")).toBe(Dashboard);
    expect(screenType("")).toBe(Dashboard);
    expect(screenType(undefined)).toBe(Dashboard);
  });

  it("keeps the console for the operator and hides it from everyone else", () => {
    expect(screenType("admin", true)).toBe(AdminView);
    expect(screenType("admin", false)).toBe(Dashboard);
  });

  it("still lands an old ebay link on Settings", () => {
    // The account mirror was folded into Settings; a bookmark to it must not
    // be the one thing that reaches a blank page.
    expect(screenType("ebay")).toBe(SettingsView);
    expect(screenType("settings")).toBe(SettingsView);
  });
});

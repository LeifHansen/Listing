/* "Create a listing" on the dashboard lands on the open uploader.
 *
 * The uploader folds itself away on arrival at Sell — one line above the
 * lists — because a seller who came to Sell came for what they are already
 * selling (theUploaderOpensFolded documents that in full, and everything it
 * guards still holds). But the dashboard's buttons are not that arrival: a
 * seller who pressed "Create a listing", "Take Photos" or "Upload Images"
 * asked for the photo box by name, and giving them a one-line bar reading
 * "Add photos" to press again is a step that exists for nobody.
 *
 * So the dashboard's create-a-listing buttons carry an intent with them, and
 * the uploader spends it as it mounts. What this pins is both halves of
 * "only from home": those buttons open it, and the intent lasts exactly one
 * arrival — every other door into Sell (the nav, the top bar, a deep link
 * into the lists, a second visit after the first spent it) lands folded, the
 * way it always has. "Open, like last time" is precisely what the fold
 * exists to stop, and a flag that outlived its own visit would be that.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import {
  afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi,
} from "vitest";

import { MotionGlobalConfig } from "framer-motion";

import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { Dashboard } from "@/views/Dashboard";
import { UploadPhase } from "./UploadPhase";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: false },
  "/api/ebay/status": { connected: false },
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

// A store with nothing in it, so the hero offers "Create a listing" rather
// than the Continue button a draft would put there. What the dashboard has
// to show is not what this file is about.
function server(url) {
  const path = String(url);
  if (path.startsWith("/api/listings")) {
    return json({
      authed: true, db: { configured: true, connected: true }, listings: [],
    });
  }
  const key = Object.keys(BASE).find((k) => path.startsWith(k));
  return key ? json(BASE[key]) : json({ detail: "Not found" });
}

function Probe({ onValue }) {
  const app = useApp();
  useEffect(() => { onValue(app); });
  return null;
}

/* The shell, cut down to the two screens this is about: the dashboard, and
   Sell with its uploader on top. Same switch the real app makes on `view`. */
function Shell() {
  const { view } = useApp();
  return view === "new" ? <UploadPhase /> : <Dashboard />;
}

let root;
let host;
let app;

async function settle() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

async function mountHome() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <Probe onValue={(v) => { app = v; }} />
          <Shell />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await settle();
}

const text = () => host.textContent || "";

function button(label) {
  return [...host.querySelectorAll("button")]
    .find((b) => (b.textContent || "").includes(label));
}

async function press(el) {
  expect(el).toBeTruthy();
  await act(async () => { el.click(); });
  await settle();
}

/** The uploader with its panel on screen, rather than the folded bar. */
const isOpen = () => text().includes("Drag photos here")
  && !!button("Browse Files");

/** The folded bar: one line, and no panel behind it. */
const isFolded = () => !!button("Add photos")
  && !text().includes("Drag photos here");

// The panel arrives through framer-motion, and in jsdom an animation never
// gets the frames it needs to finish.
let motionWasSkipping;
beforeAll(() => {
  motionWasSkipping = MotionGlobalConfig.skipAnimations;
  MotionGlobalConfig.skipAnimations = true;
});
afterAll(() => { MotionGlobalConfig.skipAnimations = motionWasSkipping; });

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(server));
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:preview-${n++}`);
  URL.revokeObjectURL = vi.fn();
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  host = null;
  app = null;
  vi.unstubAllGlobals();
});

describe("the dashboard's way into a new listing", () => {
  it("opens the uploader unfolded from Create a listing", async () => {
    await mountHome();

    await press(button("Create a listing"));

    expect(isOpen()).toBe(true);
  });

  it("does the same from the Take Photos and Upload Images tiles", async () => {
    // Three buttons, one promise: somewhere to put photos.
    for (const label of ["Take Photos", "Upload Images", "Create Listing"]) {
      await mountHome();
      await press(button(label));
      expect(isOpen(), `${label} should open the uploader`).toBe(true);
      await act(async () => { root.unmount(); });
      host.remove();
      root = null;
    }
  });

  it("leaves the fold alone for every other door into Sell", async () => {
    // The nav and the top bar call startNew() with nothing: a seller heading
    // for Sell, who gets the lists first.
    await mountHome();

    await act(async () => { app.startNew(); });
    await settle();

    expect(isFolded()).toBe(true);
  });

  it("leaves a deep link into the lists folded", async () => {
    // A dashboard tile asking for the drafts is asking for the lists.
    await mountHome();

    await act(async () => { app.openListings("drafts"); });
    await settle();

    expect(isFolded()).toBe(true);
  });

  it("spends the intent on the arrival that follows the press", async () => {
    // One visit, not a preference. A seller who opened it from the dashboard
    // and came back to Sell later — by a route that never touched the flag —
    // gets the folded bar, not "open, like last time".
    await mountHome();
    await press(button("Create a listing"));
    expect(isOpen()).toBe(true);

    await act(async () => { app.setView("dashboard"); });
    await settle();
    await act(async () => { app.setView("new"); });
    await settle();

    expect(isFolded()).toBe(true);
  });

  it("leaves the rest of the arrival exactly as it was", async () => {
    // Everything else about pressing the button is unchanged: no editor
    // session hanging around from before, and the uploader is the uploader —
    // same panel, same buttons, same hidden input the phone's library hands
    // its photos to.
    await mountHome();
    await act(async () => {
      app.setSession({ sessionId: "old", listing: { title: "Left open" } });
    });
    await settle();

    // The quick action, not the hero: with a session open the hero is the
    // Continue button, which is a different promise about a different listing.
    await press(button("Create Listing"));

    expect(app.session).toBe(null);
    expect(button("Take Photos")).toBeTruthy();
    expect(host.querySelector('input[type="file"]')).toBeTruthy();
  });
});

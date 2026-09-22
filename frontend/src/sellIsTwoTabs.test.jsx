/* "Sell" was one tab doing two jobs. It is two tabs now.
 *
 * The old screen stacked the photo uploader, the drafts grid and the entire
 * listings manager on top of each other, so whichever of the three a seller
 * came for, they scrolled past the other two to reach it. The uploader had
 * already been folded down to a one-line bar to buy room (see
 * theUploaderOpensOnTheListTab) — a workaround for two jobs sharing a screen.
 *
 * What these pin is the split itself, because every part of it is the kind
 * that rots quietly:
 *
 *  - each tab shows ITS half and not the other's. A manager that creeps back
 *    onto List rebuilds the screen this change took apart, and nothing would
 *    fail;
 *  - the deep links land on the right tab. A dashboard tile asking for drafts
 *    means List; one asking for what is live means Manage. Route them all to
 *    one place and the tiles still "work" — they just go somewhere else;
 *  - the editor opens where you already are. Both tabs can open a listing,
 *    and sending Manage's live listings to the List tab to be edited would
 *    drop the seller on a photo uploader they never asked for;
 *  - the thumb bar reaches both. Only CSS hides the bottom nav here, so this
 *    is the one place its mobile-only item list is checked at all.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MotionGlobalConfig } from "framer-motion";
import App from "@/App";
import { AppProvider, useApp, viewForTab } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { NewListing } from "@/views/NewListing";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const LISTINGS = [
  { id: "d1", status: "draft", listing: { title: "Brass desk lamp" } },
  { id: "l1", status: "published", listing: { title: "Wool camp blanket" } },
];

const BASE = {
  "/api/auth/me": { user: { id: "u1", email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: true, username: "seller" },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
  "/api/insights": { recommendations: [] },
  "/api/listings": {
    authed: true, db: { configured: true, connected: true }, listings: LISTINGS,
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
  // The single-listing read the editor does on open, before the prefix match
  // below claims it for the collection.
  const one = path.match(/\/api\/listings\/([^/?]+)$/);
  if (one) {
    const rec = LISTINGS.find((l) => l.id === one[1]);
    return rec ? json({ ...rec, conflicts: [] }) : json({ detail: "gone" }, 404);
  }
  const key = Object.keys(BASE).find((k) => path.startsWith(k));
  return key ? json(BASE[key]) : json({ detail: "Not found" }, 404);
};

let root;
let host;
/** The app store, for the navigation calls a dashboard tile would make.
 *  Only the List-screen mount below wires this up; <App/> builds its own
 *  provider tree and takes no children. */
let store;

function Probe() {
  const app = useApp();
  // Assigning a module global during render is what the hooks lint objects
  // to, rightly — an effect is the commit-time hand-off it wants.
  useEffect(() => { store = app; });
  return null;
}

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(<App />); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

/** The store on its own, for the rules that live in it rather than on a
 *  screen. <App/> builds its own providers and takes no children, so a probe
 *  can only reach the context from inside a tree the test mounts itself. */
async function mountStore() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(<ToastProvider><AppProvider><Probe /></AppProvider></ToastProvider>);
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

/** The List screen on its own, with a handle on the store beside it. */
async function mountListScreen() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider>
        <Probe /><NewListing />
      </AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const main = () => host.querySelector("main");
const mainText = () => main()?.textContent || "";

/** A nav entry by label. The sidebar renders before the thumb bar, so this is
 *  the sidebar's — the bottom bar has its own reader below. */
const navButton = (label) => [...host.querySelectorAll("nav button")]
  .find((b) => (b.textContent || "").trim().startsWith(label));

async function go(label) {
  const b = navButton(label);
  expect(b, `no nav entry labelled ${label}`).toBeTruthy();
  await act(async () => { b.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

async function press(el) {
  expect(el).toBeTruthy();
  await act(async () => { el.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

let motionWasSkipping;
beforeEach(() => {
  motionWasSkipping = MotionGlobalConfig.skipAnimations;
  MotionGlobalConfig.skipAnimations = true;
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(server));
});

afterEach(async () => {
  MotionGlobalConfig.skipAnimations = motionWasSkipping;
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  host = null;
  vi.unstubAllGlobals();
});

describe("the nav", () => {
  it("offers List and Manage where Sell used to be", async () => {
    await mount();

    expect(navButton("List")).toBeTruthy();
    expect(navButton("Manage")).toBeTruthy();
    expect(navButton("Sell")).toBeFalsy();
  });

  it("puts all five on the thumb bar, in reach and in order", async () => {
    // Only CSS hides this bar, so jsdom renders it and nothing else checks
    // its hand-picked list. Dropping Manage from it would strand half of the
    // split on desktop.
    await mount();

    const bars = [...host.querySelectorAll("nav")];
    const thumb = bars[bars.length - 1];
    const labels = [...thumb.querySelectorAll("button")]
      .map((b) => b.getAttribute("aria-label"));
    expect(labels).toEqual(["Home", "Shop", "List", "Manage", "Settings"]);
  });
});

describe("a deep link lands on the tab that owns it", () => {
  // The dashboard tiles, Shop Mode's finds and every publish return path go
  // through openListings(tab). One screen used to answer for all of them; now
  // the tab decides which, and a wrong answer is silent — the link still
  // "works", it just arrives somewhere else.
  it("sends drafts to List and everything with a lifecycle to Manage", () => {
    expect(viewForTab("drafts")).toBe("new");
    expect(viewForTab("active")).toBe("manage");
    expect(viewForTab("finds")).toBe("manage");
    expect(viewForTab("inactive")).toBe("manage");
    expect(viewForTab("all")).toBe("manage");
  });
});

describe("each tab shows its own half", () => {
  it("gives List the photos and the drafts, and no listings manager", async () => {
    await mount();
    await go("List");

    expect(mainText()).toContain("List an item");
    expect(mainText()).toContain("Drag photos here");
    expect(mainText()).toContain("Brass desk lamp");
    // The manager's tab strip is the tell — it belongs to Manage now.
    expect(mainText()).not.toContain("Sync with eBay");
  });

  it("gives Manage the listings, and no photo uploader", async () => {
    await mount();
    await go("Manage");

    expect(mainText()).toContain("Manage");
    expect(mainText()).toContain("Wool camp blanket");
    expect(mainText()).toContain("Sync with eBay");
    expect(mainText()).not.toContain("Drag photos here");
    expect(mainText()).not.toContain("Add photos");
  });

  it("titles each page once", async () => {
    // The manager used to carry its own "Listings" heading because it was a
    // section of a bigger screen. Under a page header that is two titles
    // stacked.
    await mount();
    await go("Manage");

    const headings = [...main().querySelectorAll("h1, h2")]
      .map((h) => (h.textContent || "").trim());
    expect(headings.filter((h) => h === "Manage")).toHaveLength(1);
    expect(headings).not.toContain("Listings");
  });
});

describe("a Drafts jump is a one-shot signal", () => {
  // openListings("drafts") leaves a note for the List screen: a batch is
  // running, so step aside and show the lists with a way back to it. The
  // merged screen cleared that note on its way past; with the lists gone to
  // Manage there is nobody else to, and a note left set answers for the NEXT
  // batch too — the seller starts one and its queue is hidden on arrival
  // because of a jump they made minutes ago. Nothing about that is visible:
  // the batch really is running, the lists really do show, and the queue is
  // simply not where it should be.
  it("does not hide the queue of a batch started later", async () => {
    await mountListScreen();

    // A jump to Drafts, landing on the List screen the note was meant for.
    await act(async () => { store.openListings("drafts"); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(host.textContent).toContain("List an item");

    // Now a batch begins. Its queue is what the seller should land on.
    await act(async () => { store.startBulk("job-1"); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

    expect(host.textContent).toContain("Bulk listing");
    expect(host.textContent).not.toContain("List an item");
  });
});

describe("the editor opens where you already are", () => {
  it("edits a live listing without leaving Manage", async () => {
    // Manage can open a listing, and being thrown onto the photo uploader to
    // edit it would be a worse answer than the merged screen ever gave.
    await mount();
    await go("Manage");

    const card = [...main().querySelectorAll("button, a")]
      .find((b) => (b.textContent || "").includes("Wool camp blanket"));
    await press(card);

    // The editor is up: its refine prompt is on screen and the listings grid
    // it was opened from is not.
    expect(host.querySelector('[aria-label="Refine listing with AI"]')).toBeTruthy();
    expect(mainText()).not.toContain("Sync with eBay");
    // And this is still Manage. The nav is the honest witness — `view` is
    // what the editor used to overwrite on the way in, and the seller's way
    // back out is whichever tab is lit.
    expect(navButton("Manage").getAttribute("aria-current")).toBe("page");
    expect(navButton("List").getAttribute("aria-current")).toBeNull();
    expect(mainText()).not.toContain("Drag photos here");
  });

  it("still goes somewhere when the screen cannot show an editor", async () => {
    // Home, the inbox and the notifications bell all open listings, and none
    // of them renders the editor. "Open where you already are" must not turn
    // into "open nowhere": the session would be set, the screen unchanged,
    // and the tap would look like it did nothing at all.
    await mountStore();
    expect(store.view).toBe("dashboard");

    await act(async () => { await store.openListing("l1"); });
    expect(store.session?.sessionId).toBe("l1");
    expect(store.view).toBe("new");
  });

  it("leaves the view alone when the screen can show one", async () => {
    await mountStore();
    await act(async () => { store.setView("manage"); });

    await act(async () => { await store.openListing("l1"); });
    expect(store.session?.sessionId).toBe("l1");
    expect(store.view).toBe("manage");
  });

  it("edits a draft without leaving List", async () => {
    await mount();
    await go("List");

    const card = [...main().querySelectorAll("button, a")]
      .find((b) => (b.textContent || "").includes("Brass desk lamp"));
    await press(card);

    expect(host.querySelector('[aria-label="Refine listing with AI"]')).toBeTruthy();
    expect(navButton("List").getAttribute("aria-current")).toBe("page");
    expect(navButton("Manage").getAttribute("aria-current")).toBeNull();
  });
});

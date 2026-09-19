/* Shop Mode, from the side of someone standing in a shop.
 *
 * This screen was the only view in the app with no test at all, and it is the
 * one where being wrong costs cash rather than a re-edit: the seller reads an
 * answer here and then pays for the item. Three of its claims are worth
 * holding down.
 *
 * A price that could not be looked up is not a market with nothing in it.
 * `/api/price-suggestions` fails and answers empty for two different reasons
 * and they used to render identically — "No price estimate yet" to someone
 * deciding whether to buy. lib/priceLookup exists to split them; this checks
 * Shop Mode actually routes the failure through it.
 *
 * "Buy" lands the find where the toast says it did. The Finds tab is hidden
 * while its count is zero (ListingsView's TABS filter), so jumping there
 * before the listings reload had landed sent the seller to a tab that was not
 * in the tab bar — for the first find, the only one they would ever look for.
 *
 * And a failure says one sentence. lib/api turns every failure into a
 * complete one, so the old "Couldn't add to inventory: " in front of the
 * server's own "Couldn't save that to your inventory just now" said it twice.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { ShopMode } from "@/views/ShopMode";

// The shelf scan reads frames out of a recorded video, and jsdom decodes no
// video at all — <video> fires neither onloadedmetadata nor onerror, so the
// real extractFrames would wait forever. Everything else in lib/api stays
// real (the upload, the price call, the error sentences are the subject
// here); only the decode is replaced, and it records what it was asked for.
const frameCalls = [];
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    extractFrames: vi.fn(async (file, maxFrames = 6) => {
      frameCalls.push(maxFrames);
      return Array.from({ length: maxFrames },
                        () => new Blob([new Uint8Array([1])], { type: "image/jpeg" }));
    }),
  };
});

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const LISTING = {
  title: "Pyrex Spring Blossom bowl", brand: "Pyrex", condition: "used_good",
  category_id: "870", images: ["img_000.jpg"],
};

const PRICE = {
  price: 42, low: 30, high: 55, count: 12, basis: "sold comps",
  sold_data: true,
};

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true,
                   taxonomy_configured: true },
  "/api/ebay/status": { connected: true },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
};

function json(body, status = 200) {
  return Promise.resolve({
    ok: status < 400, status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

/* A server for one scan.
 *
 * `price` is what /api/price-suggestions answers — including the shape where
 * it never got to look (`checked: false`). `addFails` makes "Buy" answer the
 * 503 the real route raises when the write did not land, with that route's
 * own sentence. `holdListings` defers the listings reload that follows a Buy
 * (never the boot one) so a test can look at the app between the two.
 */
function server(calls, { price = PRICE, addFails = false,
                         holdListings = null } = {}) {
  let listingReads = 0;
  return (url, opts = {}) => {
    const path = String(url);
    const body = () => { try { return JSON.parse(opts.body || "{}"); } catch { return null; } };
    if (path.startsWith("/api/upload")) {
      calls.push({ path: "/api/upload" });
      return json({ session_id: "sess42" });
    }
    if (path.startsWith("/api/identify/")) {
      calls.push({ path });
      return json({ listing: { ...LISTING }, confidence: "high" });
    }
    if (path.startsWith("/api/price-suggestions")) {
      calls.push({ path: "/api/price-suggestions", body: body() });
      // Never reached eBay: the request itself fails, which is what lib/api
      // turns into the `checked: false` the view has to tell apart from an
      // empty market.
      if (price === "unreachable") return json({ detail: "eBay is busy." }, 503);
      // Looked, and the market has nothing like it.
      if (price === "none") return json({ suggestion: null, checked: true });
      return json({ suggestion: price, checked: true });
    }
    if (path.startsWith("/api/shelf-scan")) {
      // How many frames actually went out, which is the half of the frame
      // count that a maxFrames argument alone would not prove.
      const sent = opts.body?.getAll ? opts.body.getAll("files").length : 0;
      calls.push({ path: "/api/shelf-scan", frames: sent });
      return json({ items: [{ name: "a brass lamp", confidence: "medium",
                              reason: "signed base", location: "top shelf" }] });
    }
    if (path.startsWith("/api/inventory/add")) {
      calls.push({ path: "/api/inventory/add", body: body() });
      if (addFails) {
        return json({ detail: "Couldn't save that to your inventory just "
                              + "now. Try again in a moment." }, 503);
      }
      return json({ ok: true, id: "sess42" });
    }
    if (path.startsWith("/api/listings")) {
      calls.push({ path: "/api/listings" });
      const answer = json({ authed: true, db: { configured: true, connected: true },
                            listings: [] });
      // The first read is the boot one; only the reload a Buy triggers is held.
      if (holdListings && ++listingReads > 1) {
        return holdListings.then(() => answer);
      }
      return answer;
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" });
  };
}

/* Where the app would send the seller, and what it is showing. `view` and
 * `listingsTab` live in the store, and openListings("finds") sets both — so
 * a jump is only observable to a component that reads them. */
function Probe() {
  const { view, listingsTab } = useApp();
  return <div data-probe>{`view=${view} tab=${listingsTab}`}</div>;
}

let root;
let host;

async function flush(turns = 2) {
  for (let i = 0; i < turns; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}

async function mount(calls = [], opts) {
  vi.stubGlobal("fetch", vi.fn(server(calls, opts)));
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <ShopMode />
          <Probe />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await flush();
}

// document.body, not the mount host: the toast renders through a portal, and
// what it says is half of what this screen owes the seller.
const text = () => document.body.textContent || "";
const probe = () => host.querySelector("[data-probe]")?.textContent || "";

function input(accept) {
  return [...host.querySelectorAll('input[type="file"]')]
    .find((i) => (i.getAttribute("accept") || "").startsWith(accept));
}

/** Hand a file to one of the two hidden pickers, the way the camera does. */
async function pick(accept, file) {
  const el = input(accept);
  expect(el).toBeTruthy();
  Object.defineProperty(el, "files", { value: [file], configurable: true });
  await act(async () => { el.dispatchEvent(new Event("change", { bubbles: true })); });
  await flush(3);
}

const photo = () =>
  new File([new Uint8Array([1, 2, 3])], "find.jpg", { type: "image/jpeg" });

const clip = () =>
  new File([new Uint8Array([1, 2, 3])], "shelf.mp4", { type: "video/mp4" });

async function press(label) {
  const button = [...host.querySelectorAll("button")]
    .find((b) => (b.textContent || "").includes(label));
  expect(button).toBeTruthy();
  await act(async () => { button.click(); });
  await flush(3);
}

beforeEach(() => {
  localStorage.clear();
  // lib/api gates every photo-bearing call on the stored AI consent; without
  // it the upload never reaches fetch (see lib/aiConsent).
  localStorage.setItem("thryft-ai-consent", "yes");
  frameCalls.length = 0;
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:preview-${n++}`);
  URL.revokeObjectURL = vi.fn();
  // jsdom has no image decoder, and its <img> fallback fires neither onload
  // nor onerror — so without this the pre-upload downscale waits forever.
  globalThis.createImageBitmap = vi.fn(async () => (
    { width: 10, height: 10, close() {} }));
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("a scan in a shop", () => {
  it("identifies the item and says what it sells for", async () => {
    const calls = [];
    await mount(calls);
    await pick("image/*", photo());

    expect(text()).toContain("Pyrex Spring Blossom bowl");
    // The estimate, and the evidence under it — a bare number on this screen
    // is a number the seller cannot judge.
    expect(text()).toContain("$42");
    expect(text()).toContain("12 comps");
    expect(text()).toContain("sold comps");
    // The comps were asked for with what the scan actually found, not a
    // bare title: brand and category are what make the answer sharp.
    const asked = calls.find((c) => c.path === "/api/price-suggestions");
    expect(asked.body.query).toContain("Pyrex");
    expect(asked.body.category_id).toBe("870");
  });

  it("does not report a failed lookup as a market with nothing in it",
     async () => {
       await mount([], { price: "unreachable" });
       await pick("image/*", photo());

       // The item was still identified: the price is best-effort and never
       // takes the scan down with it.
       expect(text()).toContain("Pyrex Spring Blossom bowl");
       expect(text()).toContain("We couldn’t check eBay’s prices just now");
       expect(text()).not.toContain("No price estimate yet");
     });

  it("still offers to buy when the market has nothing comparable", async () => {
    await mount([], { price: "none" });
    await pick("image/*", photo());

    expect(text()).toContain("No price estimate yet");
    expect(text()).not.toContain("We couldn’t check eBay’s prices");
  });
});

describe("buying a find", () => {
  it("sends the scan to inventory and lands on the tab that holds it",
     async () => {
       const calls = [];
       await mount(calls);
       await pick("image/*", photo());
       await press("Buy");

       const add = calls.find((c) => c.path === "/api/inventory/add");
       expect(add.body.session_id).toBe("sess42");
       expect(add.body.listing.title).toBe("Pyrex Spring Blossom bowl");
       expect(probe()).toContain("tab=finds");
       expect(text()).toContain("It's under Finds on the Sell tab");
     });

  it("waits for the reload before jumping, so the tab exists when it gets there",
     async () => {
       let release;
       const held = new Promise((r) => { release = r; });
       const calls = [];
       await mount(calls, { holdListings: held });
       await pick("image/*", photo());
       await press("Buy");

       // Mid-Buy: the write has landed and the reload is still in flight.
       // Jumping here is what put the seller on a tab the tab bar was still
       // filtering out, because the Finds count was the pre-Buy zero.
       expect(calls.some((c) => c.path === "/api/inventory/add")).toBe(true);
       expect(probe()).not.toContain("tab=finds");
       // And the find is still on screen for the whole wait: clearing it
       // first put "Nothing scanned yet" in the gap, which on a shop's
       // connection reads as the Buy having thrown the item away.
       expect(text()).toContain("Pyrex Spring Blossom bowl");
       expect(text()).not.toContain("Nothing scanned yet");

       await act(async () => { release(); });
       await flush(3);

       expect(probe()).toContain("tab=finds");
     });

  it("says a failure once, in the server's own words", async () => {
    await mount([], { addFails: true });
    await pick("image/*", photo());
    await press("Buy");

    expect(text()).toContain("Couldn't save that to your inventory just now");
    // Not "Couldn't add to inventory: Couldn't save that to your inventory…".
    expect(text()).not.toContain("Couldn't add to inventory");
    // And the find is still on screen: a Buy that did not land must leave
    // something to press again.
    expect(text()).toContain("Pyrex Spring Blossom bowl");
  });
});

describe("a shelf", () => {
  it("spends every frame the scan is allowed", async () => {
    const calls = [];
    await mount(calls);
    await pick("video/*", clip());

    // /api/shelf-scan reads files[:8] and charges per run, not per frame.
    expect(frameCalls).toEqual([8]);
    expect(calls.find((c) => c.path === "/api/shelf-scan").frames).toBe(8);
  });

  it("flags what is worth a closer look, and where it was", async () => {
    await mount();
    await pick("video/*", clip());

    expect(text()).toContain("1 item worth a closer look");
    expect(text()).toContain("a brass lamp");
    expect(text()).toContain("top shelf");
  });
});

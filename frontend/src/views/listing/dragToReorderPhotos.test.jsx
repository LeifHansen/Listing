/**
 * The photo grid reorders by drag, and nothing else sits on the photo.
 *
 * The tile used to carry rotate, "Main", ‹ › and a hover Edit as well as the
 * bin; on a phone's three-across grid the six of them covered the picture.
 * Now the bin is the only control, a tap opens the studio, and the order is
 * changed by carrying a photo — press and hold on a phone, straight away with
 * a mouse — or with the arrow keys on a focused tile.
 *
 * The part that has to be exact is the touch gesture. The grid lives on a
 * page that scrolls, so a finger that moves before the hold is up is
 * scrolling and must get the page, not a lifted photo; a finger that rests
 * first gets the photo. Getting either wrong is the grid that cannot be
 * scrolled, or the photo that cannot be moved — both of which have been
 * shipped here before.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { MotionGlobalConfig } from "framer-motion";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { useListingForm } from "./useListingForm";
import { PhotosCard } from "./cards";
import { HOLD_MS, SLOP } from "./useDragReorder";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const IMAGES = ["img_000.jpg", "img_001.jpg", "img_002.jpg", "img_003.jpg"];
const TILE = 100;   // px: the slots are laid out as a 3-across grid of these

function ok(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

let edited = [];
function Probe({ onValue }) {
  const app = useApp();
  const form = useListingForm();
  useEffect(() => { onValue({ app, form }); });
  return (
    <PhotosCard
      w={form} onEdit={(name) => edited.push(name)} onDelete={() => {}}
      onRemoveVideo={() => {}}
    />
  );
}

let root;
let host;
let fetches;

let motionWasSkipping;
beforeAll(() => {
  motionWasSkipping = MotionGlobalConfig.skipAnimations;
  MotionGlobalConfig.skipAnimations = true;
});
afterAll(() => { MotionGlobalConfig.skipAnimations = motionWasSkipping; });

beforeEach(() => {
  localStorage.clear();
  edited = [];
  fetches = [];
  vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
    fetches.push({ url: String(url), opts });
    if (String(url).includes("/images/order")) return ok({ images: JSON.parse(opts.body).images });
    return ok({});
  }));
  // jsdom lays nothing out, so every slot reports where the grid would put
  // it: three across, TILE px square, no gap.
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function rect() {
    const i = Number(this.dataset?.slot);
    if (Number.isNaN(i)) return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
    const left = (i % 3) * TILE;
    const top = Math.floor(i / 3) * TILE;
    return { left, top, right: left + TILE, bottom: top + TILE, width: TILE, height: TILE };
  });
});

afterEach(async () => {
  vi.useRealTimers();
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  let value = null;
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><Probe onValue={(v) => { value = v; }} /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => {
    value.app.setSession({ sessionId: "s1", listing: { images: [...IMAGES] } });
  });
  tagSlots();
  return () => value;
}

/** The tiles, in grid order. */
const tiles = () => [...host.querySelectorAll('[aria-label^="Photo "]')]
  .map((b) => b.closest("div"));

/** Slot numbers on the tiles, for the rect stub above. */
function tagSlots() {
  tiles().forEach((t, i) => { t.dataset.slot = String(i); });
}

/** The photos on screen, in the order the grid shows them. */
const shown = () => [...host.querySelectorAll("img")]
  .map((img) => img.getAttribute("src") || "")
  .filter((s) => s.includes("img_"))
  .map((s) => s.match(/(img_\d+\.jpg)/)[1]);

/** The order the server was last asked to keep. */
function savedOrder() {
  const call = [...fetches].reverse().find((f) => f.url.includes("/images/order"));
  return call ? JSON.parse(call.opts.body).images : null;
}

/** Centre of slot i. */
const at = (i) => ({ clientX: (i % 3) * TILE + TILE / 2, clientY: Math.floor(i / 3) * TILE + TILE / 2 });

function pointer(el, type, pointerType, pos, extra = {}) {
  const ev = new window.PointerEvent(type, {
    bubbles: true, cancelable: true, pointerId: 1, pointerType, button: 0,
    isPrimary: true, ...pos, ...extra,
  });
  act(() => { el.dispatchEvent(ev); });
  return ev;
}

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

describe("the photo tile", () => {
  it("carries only the bin — no rotate, Main, nudge or Edit buttons", async () => {
    await mount();
    const tile = tiles()[1];
    const buttons = [...tile.querySelectorAll("button")];
    const labels = buttons.map((b) => b.getAttribute("aria-label"));
    expect(labels.filter((l) => l.startsWith("Delete photo"))).toHaveLength(1);
    expect(labels.some((l) => /rotate|main image|earlier|later/i.test(l))).toBe(false);
    expect(tile.textContent).not.toContain("Edit");
    // Only the first photo wears the badge, which is a mark and not a button.
    expect(tiles()[0].textContent).toContain("Main");
    expect(tile.textContent).not.toContain("Main");
  });

  it("opens the studio on a tap", async () => {
    await mount();
    const tile = tiles()[2];
    pointer(tile, "pointerdown", "touch", at(2));
    pointer(tile, "pointerup", "touch", at(2));
    act(() => { tile.querySelector('[aria-label^="Photo "]').click(); });
    expect(edited).toEqual(["img_002.jpg"]);
    expect(savedOrder()).toBeNull();
  });
});

describe("dragging a photo with a mouse", () => {
  it("moves it to the slot it is dropped on, and saves that order", async () => {
    await mount();
    const tile = tiles()[0];
    pointer(tile, "pointerdown", "mouse", at(0));
    // Past the slop: the tile lifts, and the grid previews the order.
    pointer(tile, "pointermove", "mouse", { clientX: at(0).clientX + SLOP + 2, clientY: at(0).clientY });
    pointer(tile, "pointermove", "mouse", at(2));
    expect(shown()).toEqual(["img_001.jpg", "img_002.jpg", "img_000.jpg", "img_003.jpg"]);
    expect(savedOrder()).toBeNull();

    pointer(tile, "pointerup", "mouse", at(2));
    await settle();
    expect(savedOrder()).toEqual(["img_001.jpg", "img_002.jpg", "img_000.jpg", "img_003.jpg"]);
    expect(shown()).toEqual(["img_001.jpg", "img_002.jpg", "img_000.jpg", "img_003.jpg"]);
    // The release that ended the drag is not a tap on the photo.
    act(() => { tile.querySelector('[aria-label^="Photo "]').click(); });
    expect(edited).toEqual([]);
  });

  it("is a click, not a drag, below the slop", async () => {
    await mount();
    const tile = tiles()[0];
    pointer(tile, "pointerdown", "mouse", at(0));
    pointer(tile, "pointermove", "mouse", { clientX: at(0).clientX + 2, clientY: at(0).clientY + 2 });
    pointer(tile, "pointerup", "mouse", { clientX: at(0).clientX + 2, clientY: at(0).clientY + 2 });
    act(() => { tile.querySelector('[aria-label^="Photo "]').click(); });
    expect(edited).toEqual(["img_000.jpg"]);
    expect(savedOrder()).toBeNull();
  });

  it("puts everything back when the pointer is cancelled", async () => {
    await mount();
    const tile = tiles()[0];
    pointer(tile, "pointerdown", "mouse", at(0));
    pointer(tile, "pointermove", "mouse", at(3));
    expect(shown()[3]).toBe("img_000.jpg");
    pointer(tile, "pointercancel", "mouse", at(3));
    expect(shown()).toEqual(IMAGES);
    expect(savedOrder()).toBeNull();
  });
});

describe("dragging a photo with a finger", () => {
  it("lets a finger that moves before the hold scroll the page", async () => {
    await mount();
    const tile = tiles()[1];
    pointer(tile, "pointerdown", "touch", at(1));
    pointer(tile, "pointermove", "touch", { clientX: at(1).clientX, clientY: at(1).clientY + SLOP + 10 });
    // Nothing lifted, so the page's own touchmove is left alone.
    const scroll = new window.TouchEvent("touchmove", { bubbles: true, cancelable: true });
    act(() => { document.dispatchEvent(scroll); });
    expect(scroll.defaultPrevented).toBe(false);
    expect(shown()).toEqual(IMAGES);
    pointer(tile, "pointerup", "touch", { clientX: at(1).clientX, clientY: at(1).clientY + 60 });
    expect(savedOrder()).toBeNull();
  });

  it("lifts a photo under a finger that rests first, and holds the page still", async () => {
    await mount();
    vi.useFakeTimers();
    const tile = tiles()[3];
    pointer(tile, "pointerdown", "touch", at(3));
    act(() => { vi.advanceTimersByTime(HOLD_MS + 1); });

    // Lifted: the slot shows as the place it came from, and a scroll is held off.
    const scroll = new window.TouchEvent("touchmove", { bubbles: true, cancelable: true });
    act(() => { document.dispatchEvent(scroll); });
    expect(scroll.defaultPrevented).toBe(true);

    pointer(tile, "pointermove", "touch", at(0));
    expect(shown()).toEqual(["img_003.jpg", "img_000.jpg", "img_001.jpg", "img_002.jpg"]);
    pointer(tile, "pointerup", "touch", at(0));
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(savedOrder()).toEqual(["img_003.jpg", "img_000.jpg", "img_001.jpg", "img_002.jpg"]);

    // Released: the page scrolls again.
    const later = new window.TouchEvent("touchmove", { bubbles: true, cancelable: true });
    act(() => { document.dispatchEvent(later); });
    expect(later.defaultPrevented).toBe(false);
  });

  it("does not start a drag from the bin", async () => {
    await mount();
    vi.useFakeTimers();
    const tile = tiles()[1];
    const bin = tile.querySelector('[aria-label="Delete photo 2"]');
    pointer(bin, "pointerdown", "touch", at(1));
    act(() => { vi.advanceTimersByTime(HOLD_MS + 1); });
    pointer(bin, "pointermove", "touch", at(0));
    expect(shown()).toEqual(IMAGES);
    pointer(bin, "pointerup", "touch", at(0));
    expect(savedOrder()).toBeNull();
  });
});

describe("moving a photo from the keyboard", () => {
  it("takes arrow keys and Home/End on a focused tile", async () => {
    await mount();
    const key = (slot, k) => act(() => {
      tiles()[slot].dispatchEvent(new window.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
    });
    key(1, "ArrowRight");
    await settle();
    expect(savedOrder()).toEqual(["img_000.jpg", "img_002.jpg", "img_001.jpg", "img_003.jpg"]);
    tagSlots();
    key(2, "Home");
    await settle();
    expect(savedOrder()).toEqual(["img_001.jpg", "img_000.jpg", "img_002.jpg", "img_003.jpg"]);
    // Off the end is nothing, not a crash and not a save.
    const before = fetches.length;
    key(0, "ArrowLeft");
    await settle();
    expect(fetches.length).toBe(before);
  });
});

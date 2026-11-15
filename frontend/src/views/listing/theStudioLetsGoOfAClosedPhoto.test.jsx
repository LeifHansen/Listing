/**
 * Closing the studio gives the photo's canvases back.
 *
 * The studio is one dialog that stays mounted for as long as the editor is
 * open, and every photo opened in it builds a layer stack plus a history
 * whose operations hold two full-size canvases each. Nothing ever let go of
 * them: a closed photo's stack sat in memory until a collector found it, and
 * the iOS shell caps the canvas memory a page may hold. Closing (or moving to
 * another photo) now releases the stack it was editing, and opening a photo
 * again still works from a clean slate.
 *
 * Which leaves a window, between opening a photo and its pixels landing,
 * where there is no stack at all and the canvas is blank. Save in that
 * window would write the blank canvas over the photo's file, so it waits.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/Toaster";
import { ImageEditor } from "./ImageEditor";
import { releaseLayers } from "./editorLayers";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// The real release, watched: the test reads which stack it was handed.
vi.mock("./editorLayers", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, releaseLayers: vi.fn(real.releaseLayers) };
});

// Stood in for, as in studioRotate.test.jsx: the studio reads only whether
// background removal is on.
vi.mock("@/store", () => ({
  useApp: () => ({
    bgRemoval: { available: false, active: false },
    loadBgRemovalStatus: () => {},
    openSettings: () => {},
  }),
}));

// jsdom never loads an <img>; a photo of 100x80 "arrives" on the next tick
// — or, while `holding`, only when the test lets it.
let holding = false;
const held = [];
class FakeImage {
  set src(v) {
    this._src = v;
    this.naturalWidth = 100;
    this.naturalHeight = 80;
    const land = () => this.onload?.();
    if (holding) held.push(land);
    else setTimeout(land, 0);
  }
  get src() { return this._src; }
}

let root;
let host;

beforeEach(() => {
  releaseLayers.mockClear();
  holding = false;
  held.length = 0;
  vi.stubGlobal("Image", FakeImage);
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve({}),
    text: () => Promise.resolve("{}"),
  })));
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  vi.unstubAllGlobals();
});

async function show(name) {
  await act(async () => {
    root.render(
      <ToastProvider>
        <ImageEditor sessionId="s1" name={name} onClose={() => {}} onSaved={() => {}} />
      </ToastProvider>,
    );
  });
  // The photo lands.
  await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
}

const tool = (label) => [...document.body.querySelectorAll('[role="dialog"] button')]
  .find((b) => (b.textContent || "").trim() === label);
const frame = () => {
  const c = document.body.querySelector('[role="dialog"] canvas');
  return [c.width, c.height];
};

/** The stack released last, and every canvas it and its history held. */
function lastReleased() {
  const calls = releaseLayers.mock.calls.filter(([layers]) => layers);
  const [layers, history] = calls.at(-1);
  return [
    layers.original, layers.cutout, layers.mask, layers.paint,
    ...[...history.undo, ...history.redo]
      .filter((e) => e.kind === "operation")
      .flatMap((e) => [e.before.original, e.before.cutout,
                       e.after.original, e.after.cutout]),
  ];
}

describe("the studio and a photo it has finished with", () => {
  it("zeroes the photo's layers and history when it closes", async () => {
    await show("img_000.jpg");
    // One operation, so the history holds snapshots too.
    act(() => { tool("Rotate").click(); });
    expect(releaseLayers.mock.calls.some(([layers]) => layers)).toBe(false);

    await show(null);
    const held = lastReleased();
    expect(held).toHaveLength(8);
    expect(held.every((c) => c.width === 0 && c.height === 0)).toBe(true);
  });

  it("lets go of one photo when another is opened in its place", async () => {
    await show("img_000.jpg");
    await show("img_001.jpg");
    expect(lastReleased().every((c) => c.width === 0)).toBe(true);
    // The new photo is on the canvas, with nothing to undo.
    expect(frame()).toEqual([100, 80]);
    expect(tool("Undo").disabled).toBe(true);
  });

  it("opens a photo again from a clean slate", async () => {
    await show("img_000.jpg");
    act(() => { tool("Rotate").click(); });
    await show(null);
    await show("img_000.jpg");
    expect(frame()).toEqual([100, 80]);
    expect(tool("Undo").disabled).toBe(true);
    // ...and edits again as before.
    act(() => { tool("Rotate").click(); });
    expect(frame()).toEqual([80, 100]);
  });
});

describe("a photo the studio is still loading", () => {
  it("is not saved over until it has landed", async () => {
    // jsdom has no encoder; any canvas exports as one small JPEG.
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(
      (cb) => cb(new Blob(["x"], { type: "image/jpeg" })));
    const saves = () => fetch.mock.calls
      .filter(([url]) => String(url).includes("/api/edit-image"));
    holding = true;
    await show("img_000.jpg");

    await act(async () => { tool("Save").click(); });
    expect(saves()).toHaveLength(0);

    await act(async () => { held.forEach((land) => land()); });
    await act(async () => { tool("Save").click(); });
    expect(saves()).toHaveLength(1);
    HTMLCanvasElement.prototype.toBlob.mockRestore();
  });
});

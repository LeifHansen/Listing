/**
 * Rotate lives in the studio.
 *
 * The photo tile used to carry a one-tap rotate that asked the server to
 * rewrite the file at once. The tile's buttons gave way to drag-to-reorder,
 * so the turn moved in here — and here it follows the studio's rule rather
 * than the tile's: it turns the canvas, it is one undoable step, and nothing
 * is written until Save. A seller who rotates and cancels has changed
 * nothing on the server.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/Toaster";
import { ImageEditor } from "./ImageEditor";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// The studio reads one thing from the app store: whether background removal
// is switched on, which decides whether Remove BG shows. Stood in for here
// rather than mounting the real AppProvider, whose boot fetches would break
// this file's point that nothing reaches the server before Save.
vi.mock("@/store", () => ({
  useApp: () => ({
    bgRemoval: { available: false, active: false },
    loadBgRemovalStatus: () => {},
    openSettings: () => {},
  }),
}));

let root;
let host;
let fetches;

// jsdom never loads an <img>; a photo of 100x80 "arrives" on the next tick.
class FakeImage {
  set src(v) {
    this._src = v;
    this.naturalWidth = 100;
    this.naturalHeight = 80;
    setTimeout(() => this.onload?.(), 0);
  }
  get src() { return this._src; }
}

beforeEach(() => {
  fetches = [];
  vi.stubGlobal("Image", FakeImage);
  vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
    fetches.push({ url: String(url), opts });
    return Promise.resolve({
      ok: true, status: 200,
      headers: { get: () => "application/json" },
      json: () => Promise.resolve({}),
      text: () => Promise.resolve("{}"),
    });
  }));
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  vi.unstubAllGlobals();
});

async function openStudio() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <ImageEditor sessionId="s1" name="img_000.jpg" onClose={() => {}} onSaved={() => {}} />
      </ToastProvider>,
    );
  });
  // The photo lands.
  await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
}

const tool = (label) => [...document.body.querySelectorAll('[role="dialog"] button')]
  .find((b) => (b.textContent || "").trim() === label);
const canvas = () => document.body.querySelector('[role="dialog"] canvas');
const frame = () => [canvas().width, canvas().height];
const click = (el) => act(() => { el.click(); });

describe("the studio's Rotate", () => {
  it("is on the toolbar and turns the frame a quarter clockwise, on the canvas", async () => {
    await openStudio();
    expect(frame()).toEqual([100, 80]);
    click(tool("Rotate"));
    expect(frame()).toEqual([80, 100]);
    // Nothing went to the server: the studio writes on Save and not before.
    expect(fetches.filter((f) => f.url.includes("/api/"))).toHaveLength(0);
  });

  it("is one undoable step, like crop", async () => {
    await openStudio();
    expect(tool("Undo").disabled).toBe(true);
    click(tool("Rotate"));
    expect(tool("Undo").disabled).toBe(false);
    click(tool("Undo"));
    expect(frame()).toEqual([100, 80]);
    click(tool("Redo"));
    expect(frame()).toEqual([80, 100]);
  });

  it("comes back round after four turns", async () => {
    await openStudio();
    for (let i = 0; i < 4; i++) click(tool("Rotate"));
    expect(frame()).toEqual([100, 80]);
  });
});

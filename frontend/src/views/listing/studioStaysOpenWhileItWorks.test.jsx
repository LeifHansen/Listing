/**
 * The studio stays open while a background removal is out, and a result
 * only lands on the photo it was asked about.
 *
 * Cancel was already disabled for this, because closing mid-inference and
 * opening another photo is how a cut-out landed on the wrong one. The
 * dialog's own X, Escape and a click on the backdrop still closed it. And
 * the check on the way back compared file names alone, which every listing
 * shares (img_000.jpg is every listing's first photo).
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/Toaster";
import { ImageEditor } from "./ImageEditor";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// Cloud removal switched on, so Remove BG goes to the server and waits.
vi.mock("@/store", () => ({
  useApp: () => ({
    bgRemoval: { available: true, active: true, budget_exhausted: false },
    loadBgRemovalStatus: () => {},
    openSettings: () => {},
  }),
}));
// This device cannot cut, which is what sends the cut to the server.
vi.mock("@/lib/bgRemoval/useLocalCutout", () => ({
  useLocalCutout: () => ({ usable: false }),
}));

let root;
let host;
let answerCut;

class FakeImage {
  set src(v) {
    this._src = v;
    this.naturalWidth = 100;
    this.naturalHeight = 80;
    setTimeout(() => this.onload?.(), 0);
  }
  get src() { return this._src; }
}

function json(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

beforeEach(() => {
  vi.stubGlobal("Image", FakeImage);
  // jsdom has no canvas encoder; the studio sends what is on the canvas.
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(
    function toBlob(done, type) { done(new Blob(["x"], { type: type || "image/png" })); });
  vi.stubGlobal("fetch", vi.fn((url) => {
    if (String(url).includes("/api/image/remove-bg")) {
      return new Promise((resolve) => {
        answerCut = () => resolve(json({ image: "data:image/png;base64,AAAA" }));
      });
    }
    return json({});
  }));
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function render(props) {
  if (!root) {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
  }
  await act(async () => {
    root.render(<ToastProvider><ImageEditor onSaved={() => {}} {...props} /></ToastProvider>);
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
}

const dialog = () => document.body.querySelector('[role="dialog"]');
const tool = (label) => [...dialog().querySelectorAll("button")]
  .find((b) => (b.textContent || "").trim() === label);
const undo = () => tool("Undo");

async function startRemoval() {
  await act(async () => { tool("Remove BG").click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
}

async function finishRemoval() {
  await act(async () => { answerCut(); await new Promise((r) => setTimeout(r, 20)); });
}

describe("a background removal in flight", () => {
  it("holds the studio open against the X, Escape and the backdrop", async () => {
    const onClose = vi.fn();
    await render({ sessionId: "s1", name: "img_000.jpg", onClose });
    await startRemoval();

    await act(async () => { dialog().querySelector('button[aria-label="Close"]').click(); });
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    const scrim = dialog().parentElement;
    await act(async () => {
      scrim.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
      scrim.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(onClose).not.toHaveBeenCalled();

    await finishRemoval();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("lands on the photo it was asked about", async () => {
    await render({ sessionId: "s1", name: "img_000.jpg", onClose: () => {} });
    await startRemoval();
    await finishRemoval();
    // The cut is one undoable step on this photo.
    expect(undo().disabled).toBe(false);
  });

  it("does not land on another listing's photo of the same name", async () => {
    await render({ sessionId: "s1", name: "img_000.jpg", onClose: () => {} });
    await startRemoval();
    await render({ sessionId: "s2", name: "img_000.jpg", onClose: () => {} });
    await finishRemoval();
    expect(undo().disabled).toBe(true);
  });
});

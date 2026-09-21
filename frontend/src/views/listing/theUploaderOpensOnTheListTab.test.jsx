/* The List tab opens on the drop zone. Everywhere else, the uploader folds.
 *
 * This box used to live at the top of "Sell", above the drafts strip AND the
 * whole listing manager, so a seller who came to look at what they were
 * already selling scrolled past a box asking for photos every single visit.
 * The answer then was to fold it down to one line and let the lists start the
 * screen. The answer now is that the lists have their own tab (Manage), so on
 * List the box can be the box again — `defaultOpen` says so, and that is the
 * only thing the tab has to pass.
 *
 * The fold itself has not gone anywhere, and neither has anything it must not
 * cost —
 *
 *  - the drop gesture, which the big dashed box spent its whole life
 *    teaching. Photos dropped on the folded bar land in the pile;
 *  - the library picker, which hands photos straight to a hidden input that
 *    has to exist whether the panel is open or not (this is the mobile bug
 *    in photosPickedFromTheLibraryReachTheGrid, one fold away from coming
 *    back);
 *  - a pile the seller has staged. The photos, the notes box and the button
 *    that starts the AI must never end up behind a bar reading "Add photos",
 *    which is how a seller loses a shoot they thought was queued.
 *
 * And the state is still not remembered: every mount starts from the prop,
 * never from what was left open last time — the same trap the cutout toggle's
 * own test describes.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import {
  afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi,
} from "vitest";

import { MotionGlobalConfig } from "framer-motion";

import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { UploadPhase } from "./UploadPhase";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;

async function mountUploader({ defaultOpen = false } = {}) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider>
        <UploadPhase defaultOpen={defaultOpen} />
      </AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

/** How the List tab mounts it. */
const mountOnList = () => mountUploader({ defaultOpen: true });

const text = () => host.textContent || "";

function button(label) {
  return [...host.querySelectorAll("button")]
    .find((b) => (b.textContent || "").includes(label));
}

async function press(el) {
  expect(el).toBeTruthy();
  await act(async () => { el.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

/** The folded bar — the whole thing is the way in. */
const bar = () => button("Add photos");

/** Hand the hidden file input photos, the way the library picker does. */
async function pickPhotos(n = 1) {
  const input = host.querySelector('input[type="file"]');
  expect(input).toBeTruthy();
  const files = Array.from({ length: n }, (_, i) =>
    new File([new Uint8Array(Array(i + 1).fill(7))], `p${i}.jpg`,
             { type: "image/jpeg" }));
  Object.defineProperty(input, "files", { value: files, configurable: true });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

/** Drop photos onto `el`, the way a drag off the desktop does. */
async function dropOn(el, files) {
  expect(el).toBeTruthy();
  await act(async () => {
    const ev = new Event("drop", { bubbles: true });
    Object.defineProperty(ev, "dataTransfer", { value: { files } });
    el.dispatchEvent(ev);
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

// Tiles and the open panel come and go through framer-motion, and in jsdom an
// animation never gets the frames it needs to finish. Set and restored around
// this file only — it is a library-wide global.
let motionWasSkipping;
beforeAll(() => {
  motionWasSkipping = MotionGlobalConfig.skipAnimations;
  MotionGlobalConfig.skipAnimations = true;
});
afterAll(() => { MotionGlobalConfig.skipAnimations = motionWasSkipping; });

beforeEach(() => {
  localStorage.clear();
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:preview-${n++}`);
  URL.revokeObjectURL = vi.fn();
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  host = null;
  vi.unstubAllGlobals();
});

describe("the uploader on the List tab", () => {
  it("opens on the drop zone, not on a bar asking to be opened", async () => {
    // The whole point of the tab. A seller who tapped "List" wants to put
    // photos in; making them press a one-line bar first to reveal the box
    // they came for is the screen hiding its own purpose.
    await mountOnList();

    expect(text()).toContain("Drag photos here");
    expect(button("Browse Files")).toBeTruthy();
    expect(button("Take Photos")).toBeTruthy();
    expect(bar()).toBeFalsy();
  });

  it("still folds away for a seller who wants the drafts up", async () => {
    // Open by default is not the same as stuck open: the fold is still the
    // way to get the grid below into view.
    await mountOnList();

    await press(button("Hide"));
    expect(text()).not.toContain("Drag photos here");
    expect(bar()).toBeTruthy();
    expect(bar().getAttribute("aria-expanded")).toBe("false");

    await press(bar());
    expect(text()).toContain("Drag photos here");
  });

  it("opens on the drop zone again next visit, whatever was left folded", async () => {
    // Same rule as ever, read the other way round: the arrival state is the
    // prop, never a decision made on another visit.
    await mountOnList();
    await press(button("Hide"));
    expect(bar()).toBeTruthy();

    await act(async () => { root.unmount(); });
    host.remove();
    await mountOnList();

    expect(text()).toContain("Drag photos here");
  });
});

describe("the uploader anywhere it is not the point of the screen", () => {
  it("opens folded, as one line", async () => {
    await mountUploader();

    expect(bar()).toBeTruthy();
    expect(bar().getAttribute("aria-expanded")).toBe("false");
    // None of the panel is on screen — that is the whole point of the fold.
    expect(text()).not.toContain("Drag photos here");
    expect(button("Browse Files")).toBeFalsy();
    expect(button("Take Photos")).toBeFalsy();
  });

  it("unfolds on a press, and folds back", async () => {
    await mountUploader();

    await press(bar());
    expect(text()).toContain("Drag photos here");
    expect(button("Browse Files")).toBeTruthy();
    expect(button("Take Photos")).toBeTruthy();
    expect(button("Hide").getAttribute("aria-expanded")).toBe("true");

    await press(button("Hide"));
    expect(text()).not.toContain("Drag photos here");
    expect(bar()).toBeTruthy();
  });

  it("opens folded again on the next visit, whatever was left open", async () => {
    await mountUploader();
    await press(bar());
    expect(text()).toContain("Drag photos here");

    await act(async () => { root.unmount(); });
    host.remove();
    await mountUploader();

    expect(text()).not.toContain("Drag photos here");
    expect(bar()).toBeTruthy();
  });

  it("takes photos dropped on the folded bar", async () => {
    await mountUploader();

    await dropOn(bar(), [
      new File([new Uint8Array([1])], "dragged.jpg", { type: "image/jpeg" }),
    ]);

    // They landed in the pile, and the panel opened to show them.
    expect(text()).toContain("1 photo");
    expect(text()).toContain("Drag photos here");
    expect(host.querySelector("img[src^='blob:']")).toBeTruthy();
  });

  it("takes photos from the picker while folded", async () => {
    // The hidden input is mounted in both states: this is the mobile path,
    // where the library hands its photos straight to it.
    await mountUploader();

    await pickPhotos(2);

    expect(text()).toContain("2 photos");
    expect(button("Identify with AI")).toBeTruthy();
  });

  it("offers no fold once there are photos staged", async () => {
    await mountUploader();
    await press(bar());
    await pickPhotos(2);

    // Nothing that would put the pile, the notes box and the AI button back
    // behind a one-line bar.
    expect(button("Hide")).toBeFalsy();
    expect(text()).toContain("2 photos");
    expect(text()).toContain("Notes for the AI");
  });

  it("stays open when the last photo is taken back out", async () => {
    // A seller deleting the wrong shot is about to pick another one — the
    // uploader folding itself away under their hands is not the answer.
    await mountUploader();
    await pickPhotos(1);
    expect(text()).toContain("1 photo");

    await press([...host.querySelectorAll("button")]
      .find((b) => b.getAttribute("aria-label") === "Remove photo"));

    expect(text()).not.toContain("1 photo");
    expect(text()).toContain("Drag photos here");
  });
});

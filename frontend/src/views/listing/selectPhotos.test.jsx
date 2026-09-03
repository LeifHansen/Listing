/**
 * Dropping several photos out of the pile at once, before anything uploads.
 *
 * The upload preview gave every tile a trash button, which is one tap for one
 * wrong photo and forty taps for the half of a shoot that came out dark — and
 * a 250-photo batch is exactly where that half exists. So the grid has a
 * select mode: tap the tiles, delete the set.
 *
 * The trap these guard is the obvious implementation. Tiles are rendered from
 * an array and the natural handle is the index, but an index shifts under
 * every removal: delete {0, 2} by index and the second removal takes what
 * used to be photo 3. Selection is therefore keyed by each preview's object
 * URL, and these tests check the photos that SURVIVE, not just how many.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MotionGlobalConfig } from "framer-motion";

import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { UploadPhase } from "./UploadPhase";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// The tiles leave through AnimatePresence, and in jsdom an exit animation
// never gets the frames it needs to finish — so a deleted tile stays in the
// DOM and the grid reads back unchanged however long the test waits. Skipping
// animations makes a removal land in the DOM the way it does for a seller
// once the 180ms is up.
MotionGlobalConfig.skipAnimations = true;

let root;
let host;

async function mountUploader() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><UploadPhase /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

/** Hand the hidden file input n photos, the way the file picker would. */
async function pickPhotos(n) {
  const input = host.querySelector('input[type="file"]');
  const files = Array.from({ length: n }, (_, i) =>
    new File([new Uint8Array([1, 2, 3])], `p${i}.jpg`, { type: "image/jpeg" }));
  Object.defineProperty(input, "files", { value: files, configurable: true });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function buttons() {
  return [...host.querySelectorAll("button")];
}

function button(label) {
  return buttons().find((b) => (b.textContent || "").includes(label));
}

async function press(el) {
  expect(el).toBeTruthy();
  await act(async () => { el.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

/** The tile toggle for photo `n` (1-based, as its label reads). */
function tile(n) {
  return buttons().find((b) => {
    const l = b.getAttribute("aria-label") || "";
    return l === `Select photo ${n}` || l === `Deselect photo ${n}`;
  });
}

/** Which previews are on screen, in order — the photos that survived. */
function shown() {
  return [...host.querySelectorAll("img")]
    .map((img) => img.getAttribute("src"))
    .filter((s) => s && s.startsWith("blob:"));
}

const text = () => host.textContent || "";

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

describe("selecting photos in the upload preview", () => {
  it("offers a way in, and shows the count until then", async () => {
    await mountUploader();
    await pickPhotos(3);
    expect(text()).toContain("3 photos");
    expect(button("Select")).toBeTruthy();
    // Nothing selectable until asked: the tiles still carry their own trash.
    expect(tile(1)).toBeFalsy();
    expect(buttons().some(
      (b) => b.getAttribute("aria-label") === "Remove photo")).toBe(true);
  });

  it("deletes exactly the photos that were picked", async () => {
    await mountUploader();
    await pickPhotos(4);
    expect(shown()).toEqual([
      "blob:preview-0", "blob:preview-1", "blob:preview-2", "blob:preview-3"]);

    await press(button("Select"));
    await press(tile(1));       // preview-0
    await press(tile(3));       // preview-2
    expect(text()).toContain("2 of 4 selected");

    await press(button("Delete 2"));
    // The survivors, not just the count: deleting {0, 2} by index would have
    // taken preview-0 and then preview-3.
    expect(shown()).toEqual(["blob:preview-1", "blob:preview-3"]);
    expect(text()).toContain("2 photos");
  });

  it("revokes only the previews that left", async () => {
    await mountUploader();
    await pickPhotos(3);
    await press(button("Select"));
    await press(tile(2));
    await press(button("Delete 1"));
    // A revoked URL renders as a broken tile, so a survivor's must survive.
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-1");
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith("blob:preview-0");
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith("blob:preview-2");
  });

  it("takes the whole pile, and gives it back", async () => {
    await mountUploader();
    await pickPhotos(3);
    await press(button("Select"));
    await press(button("Select all"));
    expect(text()).toContain("3 of 3 selected");
    await press(button("Select none"));
    expect(text()).toContain("0 of 3 selected");
  });

  it("will not delete nothing", async () => {
    await mountUploader();
    await pickPhotos(2);
    await press(button("Select"));
    expect(button("Delete").disabled).toBe(true);
    await press(tile(1));
    expect(button("Delete 1").disabled).toBe(false);
  });

  it("forgets the selection on the way out", async () => {
    await mountUploader();
    await pickPhotos(3);
    await press(button("Select"));
    await press(tile(1));
    await press(button("Done"));
    expect(shown()).toHaveLength(3);          // nothing deleted

    await press(button("Select"));
    expect(text()).toContain("0 of 3 selected");
  });

  it("puts the toolbar away when the last photo goes", async () => {
    await mountUploader();
    await pickPhotos(2);
    await press(button("Select"));
    await press(button("Select all"));
    await press(button("Delete 2"));
    expect(shown()).toHaveLength(0);
    expect(button("Select")).toBeFalsy();
  });
});

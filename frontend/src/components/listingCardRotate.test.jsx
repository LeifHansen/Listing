/**
 * A photo can be turned upright from the card it is sideways on.
 *
 * Rotation lived in the full editor only: a seller scanning a grid of drafts
 * could see a photo was on its side and could do nothing about it without
 * opening the listing, finding the photo in the strip, rotating it, and
 * coming back. The card now carries the same one-tap rotate the editor's
 * tile has, with the same promises: the turn shows before the server has
 * answered, holds until the rotated file is what is on screen, and is taken
 * back if the rotate did not happen.
 *
 * The card's own trap is the cache-buster. Its thumbnail is versioned by the
 * listing's updated_at, which the server bumps in the background after a
 * rotate — and the listings refetch the rotate triggers can land BEFORE that
 * bump, handing the card the updated_at it already had. A browser reuses an
 * image it has loaded in this page for an identical URL without asking, so a
 * card that went back to that version would paint the old orientation over a
 * file that is turned, and the seller would watch the photo rotate and then
 * rotate straight back.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ListingCard } from "./ListingCard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const UPDATED = "2026-09-01T00:00:00Z";
const UPDATED_VERSION = Date.parse(UPDATED);
const ROTATED_VERSION = 1756800000123;

function draft(overrides = {}) {
  return {
    id: "d1", status: "draft", updated_at: UPDATED,
    listing: { title: "Sideways lamp", price: 12, images: ["img_000.jpg"] },
    ...overrides,
  };
}

let container;
let root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(props) {
  act(() => {
    root.render(<ListingCard item={draft()} onOpen={() => {}} {...props} />);
  });
}

const img = () => container.querySelector("img");
// The turn goes on the box around the photo, not the photo itself.
const box = () => img().parentElement;
const rotateButton = () =>
  container.querySelector('button[aria-label="Rotate photo 90°"]');

async function tap() {
  await act(async () => { rotateButton().click(); });
}

/** The browser finishing a fetch and painting those bytes. */
function paint() {
  act(() => { img().dispatchEvent(new Event("load")); });
}

describe("the rotate button on a card", () => {
  it("is there for a draft with a photo of its own", () => {
    render({ onRotate: () => Promise.resolve(ROTATED_VERSION) });
    expect(rotateButton()).toBeTruthy();
  });

  it("is not offered at all unless the caller wires it", () => {
    render({});
    expect(rotateButton()).toBeNull();
  });

  it("stays off a listing whose photos are eBay's own", () => {
    // Imported listings carry absolute image URLs and no file on the server
    // — there is nothing here the rotate route could turn.
    render({
      item: draft({ listing: { title: "From eBay", price: 5,
                               image_urls: ["https://i.ebayimg.com/x.jpg"] } }),
      onRotate: () => Promise.resolve(ROTATED_VERSION),
    });
    expect(container.querySelector("img")).toBeTruthy();
    expect(rotateButton()).toBeNull();
  });

  it("stays put on a card that offers a tick box", () => {
    // Ticking is a checkbox in the card's own corner now, not a mode that
    // takes the card's controls away — so a sideways photo on a draft the
    // seller has picked for a bulk action is still one tap from straight.
    render({ onRotate: () => Promise.resolve(ROTATED_VERSION),
             selectable: true, onSelect: () => {} });
    expect(rotateButton()).toBeTruthy();
  });

  it("sits in the row's controls in list layout", () => {
    render({ layout: "list", onRotate: () => Promise.resolve(ROTATED_VERSION) });
    const button = rotateButton();
    expect(button).toBeTruthy();
    // Beside the row, not laid over the (tiny) thumbnail.
    expect(button.className).not.toContain("absolute");
  });
});

describe("the turn", () => {
  it("shows on the tap and asks the server for this listing's main photo",
    async () => {
      const onRotate = vi.fn(() => new Promise(() => {}));   // never answers
      render({ onRotate });

      expect(box().style.transform).toBe("");
      await tap();

      expect(onRotate).toHaveBeenCalledWith("d1", "img_000.jpg");
      expect(box().style.transform).toContain("rotate(90deg)");
      // The 4:3 tile re-cuts the box to the 3:4 that turns into it, so the
      // preview is the crop the rotated file will get, not a squashed one.
      expect(box().style.width).toBe("75%");
    });

  it("only turns in a list row, whose thumbnail is already square", async () => {
    render({ layout: "list", onRotate: () => new Promise(() => {}) });
    await tap();
    expect(box().style.transform).toBe("rotate(90deg)");
    expect(box().style.width).toBe("");
  });

  it("asks for the rotated file by the version the server gave, and holds "
     + "until that file is on screen", async () => {
    render({ onRotate: () => Promise.resolve(ROTATED_VERSION) });
    expect(img().src).toContain(`?v=${UPDATED_VERSION}`);

    await tap();

    // The server has answered: the <img> now points at a URL no load of this
    // photo has used. The pixels on screen are still the old ones, so the
    // turn stays on.
    expect(img().src).toContain(`?v=${ROTATED_VERSION}`);
    expect(box().style.transform).toContain("rotate(90deg)");

    paint();

    expect(box().style.transform).toBe("");
    expect(box().style.width).toBe("");
  });

  it("is taken back when the rotate failed", async () => {
    render({ onRotate: () => Promise.reject(new Error("Couldn't rotate that photo")) });
    await tap();
    expect(box().style.transform).toBe("");
    // And the card is still asking for the file it was showing.
    expect(img().src).toContain(`?v=${UPDATED_VERSION}`);
  });
});

describe("the thumbnail's version after a rotate", () => {
  it("keeps the rotated file's own while a refetch still carries the old "
     + "updated_at", async () => {
    render({ onRotate: () => Promise.resolve(ROTATED_VERSION) });
    await tap();
    paint();

    // The listings refetch came back before the server's background bump of
    // updated_at: a fresh record object with the same timestamp.
    render({ item: draft(), onRotate: () => Promise.resolve(ROTATED_VERSION) });

    expect(img().src).toContain(`?v=${ROTATED_VERSION}`);
  });

  it("goes back to the record once its updated_at has moved on", async () => {
    render({ onRotate: () => Promise.resolve(ROTATED_VERSION) });
    await tap();
    paint();

    const later = "2026-09-01T00:00:05Z";
    render({ item: draft({ updated_at: later }),
             onRotate: () => Promise.resolve(ROTATED_VERSION) });

    expect(img().src).toContain(`?v=${Date.parse(later)}`);
    expect(img().src).not.toContain(`?v=${ROTATED_VERSION}`);
  });

  it("never repeats a version, even when the server sent none", async () => {
    render({ onRotate: () => Promise.resolve(undefined) });
    await tap();

    const m = /[?&]v=(\d+)/.exec(img().src);
    expect(m).toBeTruthy();
    expect(Number(m[1])).toBeGreaterThan(UPDATED_VERSION);
  });
});

/**
 * The "N to review" chip is a button, and it is not inside the card's button.
 *
 * Asked for as: "make this clickable from the grid/draft listing view, and
 * auto fill without going to listing detail page."
 *
 * The chip counts the item specifics the AI inferred rather than read off the
 * item. They block nothing, so the chip is the only thing on the card that
 * asks the seller to go and do something — and the only place to do it was
 * the full editor, two screens away. A seller reviewing twenty fresh drafts
 * made twenty round trips to say twenty times that the guess was fine, which
 * is how a warning becomes something you publish past.
 *
 * The trap on the way to a clickable chip is where it was drawn. The whole
 * card is one <button>, and a button inside a button is invalid HTML: the
 * browser closes the outer one early, so the chip lands OUTSIDE the card in
 * the DOM the user actually gets, and it drops out of the tab order. Every
 * other control on this card is a sibling laid over it for exactly that
 * reason (the rotate button, the bulk tick, delete/end/skip), and so is this
 * one — which is what these tests hold in place.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ListingCard } from "./ListingCard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// Two guesses and one thing the seller already owns: the chip counts two.
const SPECIFICS = [
  { name: "Brand", value: "Levi's", confidence: "medium" },
  { name: "Colour", value: "Navy", confidence: "medium" },
  { name: "Department", value: "Men", confidence: "" },
];

function draft(listing = {}) {
  return {
    id: "d1", status: "draft", updated_at: "2026-09-01T00:00:00Z",
    listing: { title: "Levi's 501", price: 45, item_specifics: SPECIFICS, ...listing },
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

function render(props = {}) {
  act(() => {
    root.render(<ListingCard item={draft()} onOpen={() => {}} {...props} />);
  });
}

// Selected the way the rest of this suite selects the card's controls: by
// the accessible name and the tooltip, which are contracts of their own.
const button = () => container.querySelector('button[aria-label^="Review "]');
const label = () =>
  container.querySelector('span[title^="AI-inferred item specifics"]');
const chip = () => button() || label();

describe("the review chip", () => {
  it("is a plain label when the caller has nowhere to send it", () => {
    render();
    expect(label()).toBeTruthy();
    expect(button()).toBe(null);
  });

  it("becomes a button when the caller can open the review", () => {
    render({ onReview: () => {} });
    expect(button()).toBeTruthy();
  });

  it("counts aspects the AI guessed, and nothing the seller answered", () => {
    render({ onReview: () => {} });
    expect(chip().textContent).toContain("2");
    expect(chip().getAttribute("aria-label"))
      .toBe("Review 2 AI-inferred item specifics");
  });

  for (const layout of ["grid", "list"]) {
    it(`is not nested inside the card's own button in ${layout} layout`, () => {
      render({ layout, onReview: () => {} });
      // The finding: nested, the browser hands back a chip the keyboard
      // cannot reach and a card whose markup closed early.
      expect(chip().closest("button")).toBe(chip());
      expect(container.querySelector("button button")).toBe(null);
    });

    it(`reports the tap without opening the listing in ${layout} layout`, () => {
      const onReview = vi.fn();
      const onOpen = vi.fn();
      render({ layout, onReview, onOpen });

      act(() => { chip().click(); });

      expect(onReview).toHaveBeenCalledTimes(1);
      expect(onReview.mock.calls[0][0].id).toBe("d1");
      // Opening the listing is the one thing this chip must never do: it is
      // the trip it exists to save.
      expect(onOpen).not.toHaveBeenCalled();
    });
  }

  it("says whether the review it opens is showing", () => {
    render({ onReview: () => {} });
    expect(chip().getAttribute("aria-expanded")).toBe("false");

    render({ onReview: () => {}, reviewing: true });
    expect(chip().getAttribute("aria-expanded")).toBe("true");
  });

  it("is drawn once, not once as a label and once as a button", () => {
    render({ onReview: () => {} });
    expect(container.querySelectorAll('button[aria-label^="Review "]'))
      .toHaveLength(1);
    expect(label()).toBe(null);
  });

  it("stands aside for a listing eBay will refuse outright", () => {
    // Advice must not sit next to (or in place of) the reason the listing
    // cannot go on eBay at all — the card's existing rule, kept.
    render({ onReview: () => {}, needsInfo: true, needsInfoWhy: "No category" });
    expect(chip()).toBeFalsy();
    expect(container.textContent).toContain("needs info");
  });

  it("is not offered on a listing that is already live", () => {
    // Once a listing is selling, the seller has stood behind it; the AI's
    // doubts about the first draft are not a fact about it.
    act(() => {
      root.render(<ListingCard onOpen={() => {}} onReview={() => {}}
        item={{ ...draft(), status: "live" }} />);
    });
    expect(chip()).toBeFalsy();
  });
});

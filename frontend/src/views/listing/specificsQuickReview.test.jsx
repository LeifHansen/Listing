/**
 * Reading the AI's guesses off the card, and answering them there.
 *
 * Asked for as: "make this clickable from the grid/draft listing view, and
 * auto fill without going to listing detail page." The chip is the clickable
 * half (components/reviewTheGuessesFromTheCard.test.jsx); this is what it
 * opens — the editor's Item specifics card reduced to the flagged rows, with
 * the same two gestures: ✓ to take a guess as it stands, or type over it and
 * take the correction in one move.
 *
 * The rule with teeth is what gets SENT. A card holds the copy of the listing
 * that the last /api/listings load handed it, and this listing has a
 * guaranteed concurrent writer: "Enrich all" fills specifics on drafts in a
 * background thread. A panel that posted `item_specifics` back wholesale
 * would erase every row that landed since it loaded — at the exact moment the
 * seller was telling us the listing looked right. So it names ASPECTS, and
 * the server applies the ✓ to the rows it is holding now.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DraftSpecificsReview, SpecificsQuickReview } from "./SpecificsQuickReview";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const loadListings = vi.hoisted(() => vi.fn(async () => {}));
vi.mock("@/store", () => ({ useApp: () => ({ loadListings }) }));

const toast = vi.hoisted(() => vi.fn());
vi.mock("@/components/ui/Toaster", () => ({ useToast: () => ({ toast }) }));

const server = vi.hoisted(() => ({ posted: [], fail: null }));
vi.mock("@/lib/api", () => ({
  postJson: vi.fn(async (path, body) => {
    server.posted.push({ path, body });
    if (server.fail) throw new Error(server.fail);
    return { ok: true };
  }),
}));

// Brand and Colour are guesses. Features is a guess too — one aspect holding
// two ticked values, because eBay's multi-selects are tick boxes. Department
// is the seller's own, and an empty Size row is the leftover a cleared field
// leaves behind: neither is anything to review.
const SPECIFICS = [
  { name: "Brand", value: "Levi's", confidence: "medium" },
  { name: "Colour", value: "Navy", confidence: "medium" },
  { name: "Features", value: "Distressed", confidence: "medium" },
  { name: "Features", value: "Button Fly", confidence: "medium" },
  { name: "Department", value: "Men", confidence: "" },
  { name: "Size", value: "", confidence: "medium" },
];

let container;
let root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  server.posted = [];
  server.fail = null;
  loadListings.mockClear();
  toast.mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(node) {
  act(() => { root.render(node); });
}

function panel(onConfirm = () => {}, specifics = SPECIFICS, props = {}) {
  render(<SpecificsQuickReview listing={{ item_specifics: specifics }}
    onConfirm={onConfirm} {...props} />);
}

const rows = () => [...container.querySelectorAll("li")];
const labels = () => rows().map((li) => li.querySelector("span").textContent);
const tick = (name) => rows()
  .find((li) => li.textContent.includes(name))
  .querySelector("button");
const box = (name) => rows()
  .find((li) => li.textContent.includes(name))
  .querySelector("input");
const readAll = () => [...container.querySelectorAll("button")]
  .find((b) => /read them all/i.test(b.textContent));

// Typing. React tracks an input's value on the node and suppresses onChange
// when it believes nothing moved, so a plain `el.value = x` reaches nothing —
// the native setter is what the tracker sees. (Same helper as
// priceQuickEdit.test.jsx.)
function type(el, value) {
  act(() => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("the review panel", () => {
  it("lists one row per aspect, not one per row of the listing", () => {
    // Four flagged ROWS, three flagged ASPECTS. Counting rows is what made
    // the editor's banner claim four guesses over three flags.
    panel();
    expect(labels()).toEqual(["Brand", "Colour", "Features"]);
    expect(container.textContent).toContain("3 AI guesses");
  });

  it("leaves a multi-select's values to the editor and offers only the ✓", () => {
    // A text box holds one answer; offering one for two ticked values would
    // quietly drop the other.
    panel();
    expect(box("Features")).toBe(null);
    expect(container.textContent).toContain("Distressed · Button Fly");
  });

  it("accepts a guess by NAME, sending no value and no specifics list", () => {
    const onConfirm = vi.fn();
    panel(onConfirm);

    act(() => { tick("Brand").click(); });

    expect(onConfirm).toHaveBeenCalledWith([{ name: "Brand", value: undefined }]);
  });

  it("sends a typed correction with the ✓", () => {
    const onConfirm = vi.fn();
    panel(onConfirm);

    type(box("Colour"), "Black");
    act(() => { tick("Colour").click(); });

    expect(onConfirm).toHaveBeenCalledWith([{ name: "Colour", value: "Black" }]);
  });

  it("takes Enter in the box as the same answer as the ✓", () => {
    const onConfirm = vi.fn();
    panel(onConfirm);

    type(box("Colour"), "Black");
    act(() => {
      box("Colour").dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });

    expect(onConfirm).toHaveBeenCalledWith([{ name: "Colour", value: "Black" }]);
  });

  it("reads an emptied box as 'accept what's there', never as a deletion", () => {
    // There is no way to say "this aspect has no answer" from a card, so a
    // cleared box must not send "" and wipe the guess being read.
    const onConfirm = vi.fn();
    panel(onConfirm);

    type(box("Colour"), "");
    act(() => { tick("Colour").click(); });

    expect(onConfirm).toHaveBeenCalledWith([{ name: "Colour", value: undefined }]);
  });

  it("offers 'I've read them all' only past one outstanding guess", () => {
    // The editor's terms, kept: a button that clears N flags without showing
    // N values is how a wrong value reaches a live listing, so it is offered
    // last and only where the values are on screen above it.
    panel();
    expect(readAll()).toBeTruthy();

    panel(() => {}, [SPECIFICS[0]]);
    expect(readAll()).toBeFalsy();
  });

  it("names every outstanding aspect when they are all read at once", () => {
    const onConfirm = vi.fn();
    panel(onConfirm);

    act(() => { readAll().click(); });

    expect(onConfirm).toHaveBeenCalledWith(
      [{ name: "Brand" }, { name: "Colour" }, { name: "Features" }]);
  });

  it("draws nothing once there is nothing flagged", () => {
    panel(() => {}, [{ name: "Brand", value: "Levi's", confidence: "" }]);
    expect(container.textContent).toBe("");
  });

  it("holds still while a save is in flight", () => {
    panel(() => {}, SPECIFICS, { saving: true });
    expect(tick("Brand").disabled).toBe(true);
    expect(readAll().disabled).toBe(true);
  });
});

describe("the panel wired to a saved draft", () => {
  const item = { id: "d1", listing: { item_specifics: SPECIFICS } };

  it("posts the aspects it read, and never the specifics list", async () => {
    render(<DraftSpecificsReview item={item} />);

    await act(async () => { tick("Brand").click(); });

    expect(server.posted).toEqual([{
      path: "/api/listings/d1/specifics/confirm",
      body: { aspects: [{ name: "Brand", value: undefined }] },
    }]);
    expect("item_specifics" in server.posted[0].body).toBe(false);
  });

  it("refreshes the cache the chip counts off", async () => {
    // Without this the seller ticks a flag and watches it stay on screen.
    render(<DraftSpecificsReview item={item} />);

    await act(async () => { tick("Brand").click(); });

    expect(loadListings).toHaveBeenCalledWith({ quiet: true });
  });

  it("says so when the save did not land", async () => {
    server.fail = "offline";
    render(<DraftSpecificsReview item={item} />);

    await act(async () => { tick("Brand").click(); });

    expect(toast).toHaveBeenCalled();
    expect(toast.mock.calls[0][0]).toContain("offline");
    expect(toast.mock.calls[0][1]).toEqual({ kind: "error" });
  });
});

describe("Enter and the ✓", () => {
  it("reach the server as the same request", () => {
    // Two gestures for one answer; they must not send two different things.
    const viaTick = vi.fn();
    panel(viaTick);
    act(() => { tick("Colour").click(); });

    const viaEnter = vi.fn();
    panel(viaEnter);
    act(() => {
      box("Colour").dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });

    expect(viaEnter.mock.calls).toEqual(viaTick.mock.calls);
  });
});

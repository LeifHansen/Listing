/**
 * The Item specifics card asks the seller to CHECK, never to fill.
 *
 * The card used to end in "Fill 8 with AI". It read as "eight fields are
 * waiting for the AI", and it was never true: the AI reads this listing's
 * photos against eBay's whole aspect list — required and recommended
 * together — at generation, so by the time the card is on screen the fill has
 * happened and the fields still blank are the ones the photos could not
 * answer. Pressing it spent a token, re-ran the same vision pass, and came
 * back "nothing new to add". It also spent that token with no confirmation at
 * all, which "Finish up" — the card directly below, running the same pass over
 * the same photos — has always asked for first.
 *
 * What is genuinely left here is review: the AI's guesses carry a ⚠, and the
 * seller clears them one at a time with "Looks right" or in one go with "I've
 * read them all". Those are the card's actions now, and the only ones.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { SpecificsCard } from "./cards";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const aspect = (name, over = {}) => ({
  name, required: false, mode: "FREE_TEXT", values: [],
  cardinality: "SINGLE", data_type: "STRING", ...over,
});

// One required aspect and five recommended: a realistic listing after the
// fill, where some answers landed and some could not.
const ASPECTS = [
  aspect("Brand", { required: true }),
  ...["Colour", "Style", "Material", "Pattern", "Fit"].map((n) => aspect(n)),
];

function stub(over = {}) {
  const specifics = over.item_specifics || [];
  return {
    categoryMeta: { aspects: ASPECTS },
    form: { item_specifics: specifics, brand: over.brand || "" },
    isLive: false,
    completion: { specifics: "todo" },
    fixTarget: null,
    publishResult: null,
    getSpecific: (name) => (specifics.find(
      (s) => s.name.trim().toLowerCase() === name.trim().toLowerCase()
        && (s.value || "").trim()) || {}).value || "",
    getSpecificValues: () => [],
    set: () => {},
    upsertSpecific: () => {},
    confirmSpecific: () => {},
    confirmAllSpecifics: () => {},
    autofillSpecifics: () => {},
    ...over,
  };
}

let root;
let host;

async function mount(w) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(<SpecificsCard w={w} />); });
  return () => host.textContent || "";
}

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  document.body.innerHTML = "";
  root = null;
});

// A listing as it arrives from generation: three aspects answered, three the
// photos could not answer. This is the exact shape that used to draw
// "Fill 3 with AI".
const AFTER_GENERATION = [
  { name: "Brand", value: "Levi's", confidence: "high" },
  { name: "Colour", value: "Blue", confidence: "medium" },
  { name: "Material", value: "Denim", confidence: "medium" },
];

describe("the Item specifics card", () => {
  it("never offers to re-run the fill that already ran", async () => {
    const text = await mount(stub({ item_specifics: AFTER_GENERATION }));
    expect(text()).not.toMatch(/Fill \d+ with AI/);
    expect(text()).not.toMatch(/with AI/);
  });

  it("offers nothing to press even when every aspect is blank", async () => {
    // A blank grid is not evidence the AI has not looked — it is what a
    // listing whose photos cannot answer its category looks like AFTER it
    // has. The way back for one the fill genuinely never reached is "Finish
    // up", which says what it costs first.
    const text = await mount(stub({ item_specifics: [] }));
    expect(text()).not.toMatch(/with AI/);
  });

  it("still lets the seller add a specific eBay's list doesn't carry", async () => {
    const text = await mount(stub({ item_specifics: AFTER_GENERATION }));
    expect(text()).toContain("Add specific");
  });

  it("asks them to check the guesses instead, all at once...", async () => {
    const text = await mount(stub({ item_specifics: AFTER_GENERATION }));
    expect(text()).toContain("2 AI guesses");
    expect(text()).toContain("I've read them all");
  });

  it("...or one at a time", async () => {
    const text = await mount(stub({
      item_specifics: [{ name: "Colour", value: "Blue", confidence: "medium" }],
    }));
    expect(text()).toContain("Looks right");
  });

  it("says what a blank box means, now that nothing offers to fill it", async () => {
    // The question the removed button answered wrongly. A seller looking at
    // three empty recommended fields has to know they are the AI's answer —
    // "your photos didn't show this" — and not a queue waiting on a press.
    const text = await mount(stub({ item_specifics: AFTER_GENERATION }));
    expect(text()).toContain("The AI filled what your photos showed");
    expect(text()).toContain("the blanks are ones only you can answer");
  });

  it("drops that line once there is nothing left blank", async () => {
    const text = await mount(stub({
      item_specifics: [
        { name: "Brand", value: "Levi's", confidence: "high" },
        ...["Colour", "Style", "Material", "Pattern", "Fit"].map((name) => ({
          name, value: "x", confidence: "high",
        })),
      ],
    }));
    expect(text()).toContain("every one is filled");
    expect(text()).not.toContain("the blanks are ones only you can answer");
  });
});

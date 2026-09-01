/* What the bulk queue is allowed to call a possible duplicate.
 *
 * The defect these pin down: the hint fired on inventory that was merely
 * SIMILAR. A Lacoste polo and a Brooks Brothers polo share "fit", "polo",
 * "shirt" and "men" — four words, enough to clear the old bar — while the
 * brands, the model, the size and the colour all disagreed and counted for
 * nothing. A seller listing a rail of shirts got the warning on every batch.
 *
 * So the tests come in two halves, and the second half is the one that keeps
 * this honest: a detector that answers "no" to everything would pass the
 * first half alone.
 */
import { describe, expect, it } from "vitest";
import { duplicateSuspects } from "./duplicateSuspects";

let seq = 0;
function draft(title, over = {}) {
  const { specifics, ...listing } = over;
  return {
    session_id: `s${++seq}`,
    listing: {
      title,
      item_specifics: Object.entries(specifics || {})
        .map(([name, value]) => ({ name, value })),
      ...listing,
    },
  };
}

// The pairs, as titles, so a failure names the two listings it paired.
const pairs = (...drafts) => duplicateSuspects(drafts)
  .map(([a, b]) => [a.listing.title, b.listing.title]);

describe("different items that happen to look alike", () => {
  it("does not pair two brands of polo shirt — the case sellers reported", () => {
    expect(pairs(
      draft("Lacoste Regular Fit Polo Shirt Mens Size L Blue"),
      draft("Brooks Brothers 1818 Slim Fit Polo Shirt Mens Size M White"),
    )).toEqual([]);
  });

  it("does not pair them when the brand is stated rather than written first", () => {
    expect(pairs(
      draft("Regular Fit Polo Shirt Mens Size L Blue", { brand: "Lacoste" }),
      draft("Slim Fit Polo Shirt Mens Size M White", { brand: "Brooks Brothers" }),
    )).toEqual([]);
  });

  it("does not pair one brand's shirt in two colours", () => {
    expect(pairs(
      draft("Lacoste Polo Shirt Blue", { brand: "Lacoste" }),
      draft("Lacoste Polo Shirt White", { brand: "Lacoste" }),
    )).toEqual([]);
  });

  it("does not pair one shirt in two sizes — the size lives in the specifics", () => {
    expect(pairs(
      draft("Lacoste Blue Polo Shirt", { brand: "Lacoste", specifics: { Size: "L" } }),
      draft("Lacoste Blue Polo Shirt", { brand: "Lacoste", specifics: { Size: "M" } }),
    )).toEqual([]);
  });

  it("does not pair two models of the same shoe", () => {
    expect(pairs(
      draft("Nike Air Max 90 White Leather Sneakers", { brand: "Nike",
        specifics: { Model: "Air Max 90" } }),
      draft("Nike Air Max 95 White Leather Sneakers", { brand: "Nike",
        specifics: { Model: "Air Max 95" } }),
    )).toEqual([]);
  });

  it("does not pair one brand's jeans in two cuts that differ by a number", () => {
    expect(pairs(
      draft("Levis 501 Original Fit Jeans Dark Wash", { brand: "Levis" }),
      draft("Levis 505 Original Fit Jeans Dark Wash", { brand: "Levis" }),
    )).toEqual([]);
  });

  it("does not pair an iPhone 12 with an iPhone 13", () => {
    expect(pairs(
      draft("Apple iPhone 12 128GB Unlocked Smartphone Black"),
      draft("Apple iPhone 13 128GB Unlocked Smartphone Black"),
    )).toEqual([]);
  });

  it("does not pair a pile of like-for-like inventory", () => {
    expect(pairs(
      draft("Levis 501 Straight Leg Jeans Mens 34x32 Dark Wash"),
      draft("Wrangler Cowboy Cut Jeans Mens 34x32 Dark Wash"),
      draft("Lee Riders Relaxed Fit Jeans Mens 34x32 Dark Wash"),
    )).toEqual([]);
  });

  it("does not pair on category words alone", () => {
    expect(pairs(
      draft("Pyrex Glass Mixing Bowl Set Vintage Kitchen"),
      draft("Anchor Hocking Glass Mixing Bowl Set Kitchen"),
    )).toEqual([]);
  });
});

describe("one item the batch drafted twice", () => {
  it("pairs two drafts of the same jacket titled differently", () => {
    expect(pairs(
      draft("Patagonia Nano Puff Insulated Jacket Mens Medium Black",
        { brand: "Patagonia" }),
      draft("Patagonia Mens Black Nano Puff Jacket Insulated Medium",
        { brand: "Patagonia" }),
    )).toEqual([[
      "Patagonia Nano Puff Insulated Jacket Mens Medium Black",
      "Patagonia Mens Black Nano Puff Jacket Insulated Medium",
    ]]);
  });

  it("pairs across the wording drift two identify passes produce", () => {
    expect(pairs(
      draft("Sony WH-1000XM4 Wireless Noise Cancelling Headphones Black"),
      draft("Sony WH-1000XM4 Bluetooth Wireless Headphones Over Ear Black"),
    )).toHaveLength(1);
  });

  it("pairs when one draft names the size in words and the other in a letter", () => {
    expect(pairs(
      draft("Patagonia Nano Puff Jacket Black", { brand: "Patagonia",
        specifics: { Size: "Large" } }),
      draft("Patagonia Nano Puff Jacket Black", { brand: "Patagonia",
        specifics: { Size: "L" } }),
    )).toHaveLength(1);
  });

  it("pairs when one draft says Navy and the other Navy Blue", () => {
    expect(pairs(
      draft("Patagonia Nano Puff Jacket", { brand: "Patagonia",
        specifics: { Color: "Navy" } }),
      draft("Patagonia Nano Puff Jacket", { brand: "Patagonia",
        specifics: { Color: "Navy Blue" } }),
    )).toHaveLength(1);
  });

  it("still pairs when only one of the two drafts names the brand", () => {
    expect(pairs(
      draft("Patagonia Nano Puff Insulated Jacket Black", { brand: "Patagonia" }),
      draft("Patagonia Nano Puff Jacket Black Insulated"),
    )).toHaveLength(1);
  });

  it("pairs a title whose words are in a different order", () => {
    expect(pairs(
      draft("Nike Air Max 90 Essential White Leather Sneakers"),
      draft("Air Max 90 Nike Essential Leather White Sneakers"),
    )).toHaveLength(1);
  });

  it("finds every suspect pair in a batch, not just the first", () => {
    expect(pairs(
      draft("Patagonia Nano Puff Insulated Jacket Black Medium"),
      draft("Patagonia Nano Puff Jacket Insulated Black Medium"),
      draft("Fujifilm Instax Mini 11 Instant Camera Pink"),
      draft("Fujifilm Instax Mini 11 Instant Film Camera Pink"),
    )).toHaveLength(2);
  });
});

describe("drafts that say too little to judge", () => {
  it("says nothing about two short titles", () => {
    expect(pairs(draft("Blue Vase"), draft("Blue Vase"))).toEqual([]);
  });

  it("says nothing about a batch of one", () => {
    expect(pairs(draft("Patagonia Nano Puff Insulated Jacket Black"))).toEqual([]);
  });

  it("survives a draft with no title at all", () => {
    expect(pairs(
      draft(""),
      draft("Patagonia Nano Puff Insulated Jacket Black"),
    )).toEqual([]);
  });

  it("survives drafts with no listing body", () => {
    expect(duplicateSuspects([{ session_id: "a" }, { session_id: "b" }])).toEqual([]);
  });

  it("survives no drafts at all", () => {
    expect(duplicateSuspects([])).toEqual([]);
    expect(duplicateSuspects(undefined)).toEqual([]);
  });
});

/* What the app is allowed to call "blocking".
 *
 * The defect these pin down: every readiness surface had its own idea of what
 * stops a listing reaching eBay, so the answers disagreed. A draft could be
 * flagged on three cards and publish fine, or look clean and come back
 * rejected. The rules now live in one place and every screen asks it — so the
 * thing worth testing is that the list contains exactly the fields eBay
 * refuses the listing over, and nothing that merely looks unfinished.
 */
import { describe, expect, it } from "vitest";
import { blockerLabels, ebayBlockers, TITLE_MAX } from "./blockers";

function draft(over = {}) {
  return {
    title: "Nike Air Max 90 Men's 10.5 White Leather Sneakers",
    condition: "USED_GOOD",
    price: 60,
    quantity: 1,
    category_id: "15709",
    package_weight_lb: 2,
    images: ["img_000.jpg"],
    item_specifics: [],
    ...over,
  };
}

const keys = (l, opts) => ebayBlockers(l, opts).map((b) => b.key);

describe("a listing eBay would accept", () => {
  it("has nothing blocking it", () => {
    expect(ebayBlockers(draft())).toEqual([]);
  });

  it("is not blocked by an empty description — eBay falls back to the title", () => {
    expect(ebayBlockers(draft({ description: "" }))).toEqual([]);
  });

  it("is not blocked by a missing subtitle or brand", () => {
    expect(ebayBlockers(draft({ subtitle: "", brand: "" }))).toEqual([]);
  });

  it("counts eBay-hosted photos on an imported listing as photos", () => {
    expect(keys(draft({ images: [], image_urls: ["https://i.ebayimg.com/x.jpg"] })))
      .toEqual([]);
  });
});

describe("the title ceiling", () => {
  it("blocks a title one character over eBay's limit", () => {
    const blockers = ebayBlockers(draft({ title: "x".repeat(TITLE_MAX + 1) }));
    expect(blockers.map((b) => b.key)).toEqual(["title"]);
    expect(blockers[0].why).toContain("1 character");
  });

  it("accepts a title exactly at the limit", () => {
    expect(ebayBlockers(draft({ title: "x".repeat(TITLE_MAX) }))).toEqual([]);
  });

  it("blocks an empty title", () => {
    expect(keys(draft({ title: "   " }))).toEqual(["title"]);
  });
});

describe("everything else eBay refuses", () => {
  it("names a missing price", () => {
    expect(keys(draft({ price: null }))).toEqual(["price"]);
  });

  it("names a price under eBay's minimum", () => {
    expect(keys(draft({ price: 0.5 }))).toEqual(["price"]);
  });

  it("names a missing category and a missing weight separately", () => {
    expect(keys(draft({ category_id: "", package_weight_lb: 0 })))
      .toEqual(["category", "weight"]);
  });

  it("rejects a category id that isn't an eBay category number", () => {
    expect(keys(draft({ category_id: "Sneakers" }))).toEqual(["category"]);
  });

  it("names a missing photo", () => {
    expect(keys(draft({ images: [] }))).toEqual(["photos"]);
  });

  it("names too many photos", () => {
    const many = Array.from({ length: 25 }, (_, i) => `img_${i}.jpg`);
    expect(keys(draft({ images: many }))).toEqual(["photos"]);
  });

  it("names a missing condition", () => {
    expect(keys(draft({ condition: "" }))).toEqual(["condition"]);
  });
});

describe("auctions are held to their own fields", () => {
  it("wants a starting bid, not a Buy It Now price", () => {
    expect(keys(draft({ listing_format: "AUCTION", price: null })))
      .toEqual(["price"]);
    expect(keys(draft({ listing_format: "AUCTION", price: null, auction_start_price: 9.99 })))
      .toEqual([]);
  });

  it("wants both prices when the auction also has Buy It Now", () => {
    expect(keys(draft({ listing_format: "AUCTION_BIN", price: null, auction_start_price: 9.99 })))
      .toEqual(["bin_price"]);
  });
});

describe("required item specifics", () => {
  const aspects = [{ name: "Brand", required: true },
    { name: "US Shoe Size", required: true },
    { name: "Color", required: false }];

  it("blocks on the ones the category requires", () => {
    const blockers = ebayBlockers(draft(), { aspects });
    expect(blockers.map((b) => b.key)).toEqual(["specifics"]);
    expect(blockers[0].why).toContain("Brand");
  });

  it("never blocks on a recommended one", () => {
    const filled = draft({
      brand: "Nike",
      item_specifics: [{ name: "US Shoe Size", value: "10.5" }],
    });
    expect(ebayBlockers(filled, { aspects })).toEqual([]);
  });

  it("counts the listing's own brand field as the Brand aspect", () => {
    // Brand lives on the listing, not in item_specifics — reporting it
    // missing sent sellers hunting for a field that already had a value.
    const filled = draft({
      brand: "Nike",
      item_specifics: [{ name: "US Shoe Size", value: "10.5" }],
    });
    expect(ebayBlockers(filled, { aspects })).toEqual([]);
  });

  it("stays quiet when the category's aspects haven't been loaded", () => {
    // A listing card has no taxonomy. Guessing here would invent blockers
    // that the editor then can't explain.
    expect(ebayBlockers(draft(), { aspects: null })).toEqual([]);
  });
});

describe("a condition the category doesn't offer", () => {
  // eBay publishes a different condition ladder per category and refuses
  // anything else with error 25021, after the whole publish. "Used - Good"
  // (5000) exists in media categories and almost nowhere else — which is
  // exactly the grade an AI reaches for on a worn item.
  const conditions = [{ enum: "NEW", label: "New" },
    { enum: "NEW_OTHER", label: "New other" },
    { enum: "USED_EXCELLENT", label: "Used" }];

  it("blocks, and names what the category does take", () => {
    const blockers = ebayBlockers(draft(), { conditions });
    expect(blockers.map((b) => b.key)).toEqual(["condition"]);
    expect(blockers[0].why).toContain("Used");
  });

  it("passes a condition the category offers", () => {
    expect(ebayBlockers(draft({ condition: "USED_EXCELLENT" }), { conditions }))
      .toEqual([]);
  });

  it("stays quiet when nobody asked eBay", () => {
    // A card with no taxonomy loaded, or a lookup that failed. Blocking on a
    // list we never fetched would strand every draft on a network blip.
    expect(ebayBlockers(draft(), { conditions: null })).toEqual([]);
    expect(ebayBlockers(draft(), { conditions: [] })).toEqual([]);
  });
});

describe("marketplaces other than eBay", () => {
  it("doesn't gate an Etsy-only publish on eBay-only fields", () => {
    const l = draft({ category_id: "", package_weight_lb: 0 });
    expect(ebayBlockers(l, { targets: ["etsy"] })).toEqual([]);
  });

  it("still gates the fields every marketplace needs", () => {
    expect(keys(draft({ title: "", price: null }), { targets: ["etsy"] }))
      .toEqual(["title", "price"]);
  });

  it("applies eBay's rules when eBay is one of several targets", () => {
    expect(keys(draft({ package_weight_lb: 0 }), { targets: ["ebay", "etsy"] }))
      .toEqual(["weight"]);
  });
});

describe("how blockers are shown", () => {
  it("names each blocking field so a seller knows where to go", () => {
    const blockers = ebayBlockers(
      draft({ title: "", package_weight_lb: 0, category_id: "" }));
    expect(blockerLabels(blockers)).toBe("Title, eBay category, Package weight");
  });

  it("gives every blocker a jump target and a reason", () => {
    const blockers = ebayBlockers(draft({ title: "", price: null, images: [] }));
    expect(blockers.length).toBeGreaterThan(0);
    for (const b of blockers) {
      expect(b.target).toBeTruthy();
      expect(b.label).toBeTruthy();
      expect(b.why).toBeTruthy();
    }
  });
});


describe("a listing with eBay variations", () => {
  it("is blocked, because the app cannot represent it", () => {
    // A revise would send an item-level Quantity into a structure eBay says
    // ReviseItem cannot revise, and a variation reaching 0 is REMOVED from
    // the listing. See backend/tests/test_variation_listings.py.
    const blockers = ebayBlockers(draft({ has_variations: true }));
    expect(blockers.map((b) => b.key)).toEqual(["variations"]);
  });

  it("says where the seller CAN change it", () => {
    const [b] = ebayBlockers(draft({ has_variations: true }));
    expect(b.why.toLowerCase()).toContain("ebay");
  });

  it("does not hide every other problem on an ordinary listing", () => {
    expect(ebayBlockers(draft({ has_variations: false, title: "" }))
      .map((b) => b.key)).toContain("title");
  });

  it("leaves an Etsy-only publish alone", () => {
    // The variation limit is about eBay's revise contract; Etsy is a
    // different provider and this must not gate it.
    expect(ebayBlockers(draft({ has_variations: true }),
                        { targets: ["etsy"] })
      .map((b) => b.key)).not.toContain("variations");
  });
});

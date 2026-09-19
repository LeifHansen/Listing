/* A filter narrows. It does not invent, and it does not go stale.
 *
 * Those are the two claims this module makes, and they are the two a filter
 * bar gets wrong. The first: a listing the question cannot be asked of — an
 * unpriced draft, a record with no created date — has to FAIL the filter
 * rather than pass it, or "under $20" answers with every draft nobody has
 * priced and the seller re-filters the list by eye, which is the work the
 * filter exists to save. The second: a saved view stores the QUESTION, never
 * the listings that answered it, so "Needs photos" empties as the photos get
 * taken instead of going stale the first time it is used.
 */
import { describe, expect, it } from "vitest";

import {
  AGE_CHOICES, EMPTY_FILTERS, MAX_SAVED_VIEWS, VIEW_NAME_MAX, activeFilterCount,
  addSavedView, clearFilter, filterChips, filterListings, filterPrice,
  isEmptyFilters, matchesFilters, matchingViewId, needsWork, normalizeFilters,
  normalizeSavedView, normalizeSavedViews, photoCount, removeSavedView,
  sameFilters,
} from "./listingFilters.js";

const NOW = Date.parse("2026-09-19T12:00:00Z");
const daysAgo = (n) => new Date(NOW - n * 86400000).toISOString();

// A listing that passes every filter unset, so each test can name the one
// fact it is about.
const item = (listing = {}, rest = {}) => ({
  id: "x1",
  status: "published",
  created_at: daysAgo(1),
  updated_at: daysAgo(1),
  ...rest,
  listing: {
    title: "A shirt", brand: "Nike", condition: "USED_EXCELLENT",
    category_suggestion: "Clothing > Men's > Shirts",
    price: 24.99, images: ["a.jpg"],
    ...listing,
  },
});

const on = (patch) => ({ ...EMPTY_FILTERS, ...patch });
const keeps = (it, patch) => matchesFilters(it, on(patch), NOW);

describe("normalizing what arrives", () => {
  it("drops values it does not know rather than acting on them", () => {
    const f = normalizeFilters({
      format: ["AUCTION", "TELEPATHY"], condition: ["NEW", "MINT"],
      photos: "maybe", within: "3000", extra: "ignored",
    });
    expect(f.format).toEqual(["AUCTION"]);
    expect(f.condition).toEqual(["NEW"]);
    expect(f.photos).toBe("any");
    expect(f.within).toBe("");
    expect(f).not.toHaveProperty("extra");
  });

  it("answers the empty set for junk", () => {
    expect(normalizeFilters(null)).toEqual(EMPTY_FILTERS);
    expect(normalizeFilters("nope")).toEqual(EMPTY_FILTERS);
    expect(isEmptyFilters(undefined)).toBe(true);
  });

  it("orders a multi-select canonically, so two ways of picking it agree", () => {
    // What lets a saved view light up as the one showing, however the seller
    // clicked their way to it.
    const a = normalizeFilters({ format: ["AUCTION_BIN", "FIXED_PRICE"] });
    const b = normalizeFilters({ format: ["FIXED_PRICE", "AUCTION_BIN"] });
    expect(a.format).toEqual(b.format);
    expect(sameFilters(a, b)).toBe(true);
  });

  it("lets a price be typed a character at a time", () => {
    // The finding: rounding each keystroke through Number() ate the decimal
    // point — "1." came back "1", the box re-rendered as "1", and 1.50 could
    // never be typed.
    expect(normalizeFilters({ priceMin: "1." }).priceMin).toBe("1.");
    expect(normalizeFilters({ priceMin: "1.5" }).priceMin).toBe("1.5");
    expect(normalizeFilters({ priceMin: "12.99" }).priceMin).toBe("12.99");
  });

  it("keeps what can never be a price out of the box", () => {
    expect(normalizeFilters({ priceMax: "20abc" }).priceMax).toBe("20");
    expect(normalizeFilters({ priceMax: "-5" }).priceMax).toBe("5");
    expect(normalizeFilters({ priceMax: "1.2.3" }).priceMax).toBe("1.2");
  });

  it("caps a text field, which is stored on the account", () => {
    expect(normalizeFilters({ brand: "x".repeat(500) }).brand.length)
      .toBeLessThanOrEqual(60);
  });
});

describe("counting what is on", () => {
  it("counts a price RANGE as one filter, not two", () => {
    expect(activeFilterCount(on({ priceMin: "5", priceMax: "50" }))).toBe(1);
  });

  it("counts each other dimension once", () => {
    expect(activeFilterCount(on({ format: ["AUCTION"], needsWork: true })))
      .toBe(2);
  });

  it("does not count the unset ones", () => {
    expect(activeFilterCount(EMPTY_FILTERS)).toBe(0);
    expect(activeFilterCount(on({ photos: "any", within: "" }))).toBe(0);
  });

  it("clears a whole dimension from one chip, both ends of a range included", () => {
    const f = clearFilter(on({ priceMin: "5", priceMax: "50", brand: "Nike" }),
      "price");
    expect(f.priceMin).toBe("");
    expect(f.priceMax).toBe("");
    expect(f.brand).toBe("Nike");   // the other chip is not collateral
  });

  it("gives every chip a key that clears it", () => {
    const f = on({
      format: ["AUCTION"], condition: ["NEW"], brand: "Nike",
      category: "Shirts", priceMin: "5", photos: "without", within: "7",
      needsWork: true,
    });
    const chips = filterChips(f);
    expect(chips).toHaveLength(activeFilterCount(f));
    for (const c of chips) {
      expect(activeFilterCount(clearFilter(f, c.key)))
        .toBe(activeFilterCount(f) - 1);
    }
  });

  it("puts three picked conditions on ONE chip", () => {
    // Three chips for one decision reads as three filters to dismiss.
    const chips = filterChips(on({ condition: ["NEW", "USED_GOOD", "LIKE_NEW"] }));
    expect(chips).toHaveLength(1);
    expect(chips[0].label).toMatch(/New/);
    expect(chips[0].label).toMatch(/Used Good/);
  });
});

describe("the price a filter is about", () => {
  it("reads an auction at its starting bid, not at an empty `price`", () => {
    // An auction imported from eBay has no `price` at all. Read that field
    // alone and every auction in the store filters as unpriced.
    const auction = item({
      listing_format: "AUCTION", price: null, auction_start_price: 9.99,
    });
    expect(filterPrice(auction)).toBe(9.99);
    expect(keeps(auction, { priceMin: "5", priceMax: "15" })).toBe(true);
  });

  it("reads a sold listing at what it WENT for, not what it asked", () => {
    // An accepted offer settles below the ask. "Everything I sold over $50"
    // must not count a $60 ask that went for $40.
    const sold = item({ price: 60, sold_price: 40 }, { status: "sold" });
    expect(filterPrice(sold)).toBe(40);
    expect(keeps(sold, { priceMin: "50" })).toBe(false);
  });

  it("leaves an unpriced draft out of every range", () => {
    // The finding this module exists to avoid: "under $20" answering with
    // the drafts nobody has priced.
    const blank = item({ price: null }, { status: "draft" });
    expect(filterPrice(blank)).toBe(null);
    expect(keeps(blank, { priceMax: "20" })).toBe(false);
    expect(keeps(blank, { priceMin: "0" })).toBe(false);
    // ...and is still there when nobody asked about price.
    expect(keeps(blank, {})).toBe(true);
  });

  it("includes the ends of the range", () => {
    expect(keeps(item({ price: 20 }), { priceMax: "20" })).toBe(true);
    expect(keeps(item({ price: 20 }), { priceMin: "20" })).toBe(true);
    expect(keeps(item({ price: 20.01 }), { priceMax: "20" })).toBe(false);
  });
});

describe("the other dimensions", () => {
  it("treats a listing with no stored format as Buy It Now, like eBay does", () => {
    const old = item({ listing_format: undefined });
    expect(keeps(old, { format: ["FIXED_PRICE"] })).toBe(true);
    expect(keeps(old, { format: ["AUCTION"] })).toBe(false);
  });

  it("matches a brand case-insensitively, on part of it", () => {
    expect(keeps(item({ brand: "Nike" }), { brand: "nik" })).toBe(true);
    expect(keeps(item({ brand: "Nike" }), { brand: "adidas" })).toBe(false);
  });

  it("matches eBay's category path AND the seller's own store shelf", () => {
    // A seller means either: eBay's tree, or the shelf name they invented.
    expect(keeps(item(), { category: "shirts" })).toBe(true);
    expect(keeps(item({ category_suggestion: "", store_category_name: "Vintage Tees" }),
      { category: "vintage" })).toBe(true);
  });

  it("counts eBay's own photo urls as photos", () => {
    // Imported listings have no local files. Asking `images` alone reports
    // every imported listing in the store as having none.
    const imported = item({ images: [], image_urls: ["https://ebay/1.jpg"] });
    expect(photoCount(imported.listing)).toBe(1);
    expect(keeps(imported, { photos: "with" })).toBe(true);
    expect(keeps(imported, { photos: "without" })).toBe(false);
  });

  it("finds the listings with no photos at all", () => {
    const bare = item({ images: [], image_urls: [] });
    expect(keeps(bare, { photos: "without" })).toBe(true);
    expect(keeps(bare, { photos: "with" })).toBe(false);
  });

  it("measures age against the instant it is given", () => {
    expect(keeps(item({}, { created_at: daysAgo(3) }), { within: "7" })).toBe(true);
    expect(keeps(item({}, { created_at: daysAgo(30) }), { within: "7" })).toBe(false);
  });

  it("does not call a listing with no date a recent one", () => {
    const undated = item({}, { created_at: "" });
    expect(keeps(undated, { within: "7" })).toBe(false);
    expect(keeps(undated, {})).toBe(true);
  });

  it("offers only windows the predicate can read", () => {
    for (const [id] of AGE_CHOICES) {
      expect(normalizeFilters({ within: id }).within).toBe(id);
    }
  });
});

describe("the listings that still need work", () => {
  it("names the three blanks that stop a publish", () => {
    expect(needsWork(item({ price: null }, { status: "draft" }))).toBe(true);
    expect(needsWork(item({ category_suggestion: "", category_id: "" },
      { status: "draft" }))).toBe(true);
    expect(needsWork(item({ images: [], image_urls: [] },
      { status: "draft" }))).toBe(true);
  });

  it("leaves a finished draft alone", () => {
    expect(needsWork(item({}, { status: "draft" }))).toBe(false);
  });

  it("never asks it of finished business", () => {
    // A sold listing is not short of anything; an ended one is not a job.
    for (const status of ["sold", "ended"]) {
      expect(needsWork(item({ price: null, images: [] }, { status }))).toBe(false);
    }
  });

  it("takes a category id as a category", () => {
    // An imported listing carries the id without the path.
    expect(needsWork(item({ category_suggestion: "", category_id: "15687" },
      { status: "draft" }))).toBe(false);
  });
});

describe("filtering a list", () => {
  const items = [
    item({ brand: "Nike", price: 18 }, { id: "a" }),
    item({ brand: "Nike", price: 90 }, { id: "b" }),
    item({ brand: "Adidas", price: 12 }, { id: "c" }),
  ];

  it("stacks the dimensions", () => {
    const got = filterListings(items, on({ brand: "nike", priceMax: "20" }), NOW);
    expect(got.map((i) => i.id)).toEqual(["a"]);
  });

  it("hands back the same list untouched when nothing is set", () => {
    expect(filterListings(items, EMPTY_FILTERS, NOW)).toBe(items);
  });

  it("survives a missing list", () => {
    expect(filterListings(undefined, on({ brand: "nike" }), NOW)).toEqual([]);
  });
});

describe("a saved view", () => {
  const filters = on({ brand: "Nike", priceMax: "20" });

  it("stores the question, never the listings that answered it", () => {
    // The whole point: a view called "Needs photos" has to empty itself as
    // the photos get taken.
    const { views } = addSavedView([], { name: "Nike under 20", tab: "all", filters });
    expect(Object.keys(views[0]).sort())
      .toEqual(["created_at", "filters", "id", "name", "tab"]);
    expect(JSON.stringify(views[0])).not.toContain("listing");
  });

  it("keeps the tab it was saved on", () => {
    // Restoring the filters alone would land a seller on whichever tab they
    // happened to be on — a different list under the same name.
    const { views } = addSavedView([], { name: "Sold big", tab: "inactive", filters });
    expect(views[0].tab).toBe("inactive");
  });

  it("refuses a nameless one, with a sentence", () => {
    const res = addSavedView([], { name: "   ", tab: "all", filters });
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/name/i);
  });

  it("replaces one saved under a name that already exists, keeping its id", () => {
    const first = addSavedView([], { name: "Cheap", tab: "all", filters });
    const again = addSavedView(first.views, {
      name: "cheap", tab: "active", filters: on({ priceMax: "5" }),
    });
    expect(again.ok).toBe(true);
    expect(again.replaced).toBe(true);
    expect(again.views).toHaveLength(1);           // not two pills with one name
    expect(again.views[0].id).toBe(first.views[0].id);
    expect(again.views[0].tab).toBe("active");
  });

  it("says when the strip is full rather than dropping the oldest", () => {
    let views = [];
    for (let i = 0; i < MAX_SAVED_VIEWS; i += 1) {
      views = addSavedView(views, { name: `v${i}`, tab: "all", filters }).views;
    }
    const res = addSavedView(views, { name: "one more", tab: "all", filters });
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(String(MAX_SAVED_VIEWS));
    // ...and an UPDATE still lands: it needs no room.
    expect(addSavedView(views, { name: "v0", tab: "all", filters }).ok).toBe(true);
  });

  it("caps the name at what the box allows", () => {
    const { views } = addSavedView([], {
      name: "n".repeat(200), tab: "all", filters,
    });
    expect(views[0].name.length).toBe(VIEW_NAME_MAX);
  });

  it("deletes by id and leaves the rest", () => {
    const a = addSavedView([], { name: "A", tab: "all", filters }).views;
    const b = addSavedView(a, { name: "B", tab: "all", filters }).views;
    expect(removeSavedView(b, b[0].id).map((v) => v.name)).toEqual(["B"]);
  });
});

describe("views read back from the server", () => {
  it("drops one with no name — a pill nobody can read or delete", () => {
    expect(normalizeSavedView({ id: "1", filters: {} })).toBe(null);
    expect(normalizeSavedViews([{ name: "" }, { name: "Good" }]))
      .toHaveLength(1);
  });

  it("drops a second view with the same id", () => {
    // Two rows with one id is a delete that removes the wrong one.
    const views = normalizeSavedViews([
      { id: "dup", name: "First" }, { id: "dup", name: "Second" },
    ]);
    expect(views).toHaveLength(1);
    expect(views[0].name).toBe("First");
  });

  it("scrubs a filter vocabulary it no longer has", () => {
    // A view saved by an older release, or hand-edited: it may not reach a
    // predicate as something the grid would act on.
    const [v] = normalizeSavedViews([
      { id: "1", name: "Old", filters: { format: ["TELEPATHY"], gone: true } },
    ]);
    expect(v.filters).toEqual(EMPTY_FILTERS);
  });

  it("survives junk entirely", () => {
    expect(normalizeSavedViews(null)).toEqual([]);
    expect(normalizeSavedViews(["nope", 7, null])).toEqual([]);
  });

  it("keeps no more than the strip holds", () => {
    const many = Array.from({ length: 80 }, (_, i) => ({ id: `i${i}`, name: `n${i}` }));
    expect(normalizeSavedViews(many)).toHaveLength(MAX_SAVED_VIEWS);
  });
});

describe("which view the screen is showing", () => {
  const filters = on({ brand: "Nike" });
  const { views } = addSavedView([], { name: "Nike", tab: "active", filters });

  it("recognises the one whose tab AND filters are on screen", () => {
    expect(matchingViewId(views, "active", filters)).toBe(views[0].id);
  });

  it("does not claim it on another tab", () => {
    // Same filters, different list. The pill must not read as pressed.
    expect(matchingViewId(views, "inactive", filters)).toBe("");
  });

  it("lets go the moment a filter changes", () => {
    expect(matchingViewId(views, "active", on({ brand: "Nike", priceMax: "9" })))
      .toBe("");
  });

  it("answers nothing when nothing is saved", () => {
    expect(matchingViewId([], "active", filters)).toBe("");
  });
});

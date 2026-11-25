/* The category pickers offer what is actually in the store, counted. */
import { describe, expect, it } from "vitest";
import {
  categoryLeaf, ebayCategoryOptions, optionNamer, storeCategoryOptions,
} from "./categoryOptions.js";
import { NO_STORE_CATEGORY } from "./listingFilters.js";

const rec = (listing) => ({ id: Math.random().toString(36), status: "published", listing });

describe("a category's readable name", () => {
  it("is the leaf of either path shape the records carry", () => {
    expect(categoryLeaf("Clothing > Men > Shirts")).toBe("Shirts");
    expect(categoryLeaf("Clothing, Shoes & Accessories:Men:Men's Clothing:Shirts"))
      .toBe("Shirts");
    expect(categoryLeaf("Toys")).toBe("Toys");
    expect(categoryLeaf("")).toBe("");
  });
});

describe("eBay categories in use", () => {
  it("are counted off the listings, most used first", () => {
    const opts = ebayCategoryOptions([
      rec({ category_id: "1", category_suggestion: "A:Shirts" }),
      rec({ category_id: "2", category_suggestion: "B > Hats" }),
      rec({ category_id: "2", category_suggestion: "Hats" }),
      rec({ category_id: "" }),
    ]);
    expect(opts.map((o) => [o.id, o.name, o.count])).toEqual([["2", "Hats", 2], ["1", "Shirts", 1]]);
    expect(opts[0].path).toBe("B > Hats");
  });
});

describe("the seller's store shelves", () => {
  const cats = [
    { id: 11, name: "Tees", path: "Tees", level: 1 },
    { id: 12, name: "Empty shelf", path: "Empty shelf", level: 1 },
  ];

  it("keep the store's order, show empty shelves, and end with 'No store category'", () => {
    const opts = storeCategoryOptions(cats, [
      rec({ store_category_id: "11" }), rec({ store_category_id: "11" }),
      rec({ store_category_id: "" }), rec({ store_category_id: "0" }),
    ]);
    expect(opts.map((o) => [o.id, o.count])).toEqual([
      ["11", 2], ["12", 0], [NO_STORE_CATEGORY, 2],
    ]);
  });

  it("still offer a shelf a listing points at that the store no longer has", () => {
    const opts = storeCategoryOptions(cats, [
      rec({ store_category_id: "99", store_category_name: "Old shelf" }),
    ]);
    const orphan = opts.find((o) => o.id === "99");
    expect(orphan).toMatchObject({ name: "Old shelf", count: 1, orphan: true });
    expect(optionNamer(opts)("99")).toBe("Old shelf");
  });
});

import { describe, expect, it } from "vitest";
import { AUTOSAVE_FIELDS, changedFields, same, wire } from "./fieldDiff";

describe("fieldDiff", () => {
  it("names only what differs, in the shape the server stores", () => {
    const saved = { title: "Old", price: 30, quantity: 2, description: "Body" };
    const current = { title: "New ", price: "30", quantity: "2", description: "Body" };
    expect(changedFields(current, saved)).toEqual({ title: "New" });
  });

  it("does not read a typed number as an edit of the stored number", () => {
    expect(same("price", "30", 30)).toBe(true);
    expect(same("price", "30.00", 30)).toBe(true);
    expect(same("price", "", null)).toBe(true);
    expect(same("quantity", "2", 2)).toBe(true);
    expect(same("package_weight_oz", "", 0)).toBe(true);
    expect(same("package_weight_lb", "1.5", 1.5)).toBe(true);
    expect(same("ad_rate_percent", "", 0)).toBe(true);
  });

  it("treats a blank price as no price, not zero", () => {
    expect(wire("price", "")).toBeNull();
    expect(wire("price", "0")).toBe(0);
    expect(wire("purchase_price", "abc")).toBeNull();
  });

  it("compares structured fields by meaning, not key order", () => {
    const a = { who_made: "i_did", taxonomy_id: 5, tags: ["x"] };
    const b = { tags: ["x"], taxonomy_id: 5, who_made: "i_did" };
    expect(same("etsy", a, b)).toBe(true);
    expect(same("etsy", a, { ...b, taxonomy_id: 6 })).toBe(false);
    expect(same("condition_descriptors", [{ id: "1", values: ["a"] }],
                [{ values: ["a"], id: "1" }])).toBe(true);
  });

  it("compares item specifics as the rows the server keeps", () => {
    const saved = [{ name: "Brand", value: "Acme", confidence: "high" }];
    const typed = [{ name: " Brand ", value: "Acme ", confidence: "high" },
                   { name: "", value: "" }];
    expect(same("item_specifics", typed, saved)).toBe(true);
    const edited = [{ name: "Brand", value: "Acme", confidence: "" }];
    // Typing over a value clears the AI's mark, and the server stores the
    // mark -- so that IS a change worth saving.
    expect(same("item_specifics", edited, saved)).toBe(false);
  });

  it("knows every field the server's two allow-lists know", () => {
    for (const f of ["title", "price", "item_specifics", "etsy", "auction_duration",
                     "fulfillment_policy_id", "listing_format"]) {
      expect(AUTOSAVE_FIELDS).toContain(f);
    }
    for (const f of ["images", "image_urls", "videos", "sku", "ebay_listing_id",
                     "marketplaces", "status", "dirty_fields"]) {
      expect(AUTOSAVE_FIELDS).not.toContain(f);
    }
  });

  it("counts everything non-blank as a change when nothing is known", () => {
    expect(changedFields({ title: "A", price: "" }, null))
      .toEqual({ title: "A" });
  });
});

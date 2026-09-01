/* Which grade a listing lands on when its category doesn't offer the one it
   has — the browser half of backend/tests/test_condition_follows_the_category.py.

   The editor used to snap an out-of-category condition to the FIRST one eBay
   listed, which is "New" almost everywhere. That swapped a publish error for
   something worse: a worn item advertised as new. These pin the replacement
   as the nearest grade in the same family, and pin the two cases where the
   honest answer is to change nothing. */
import { describe, it, expect } from "vitest";
import { nearestCondition, conditionLabel, CONDITIONS } from "./conditions";

// eBay's answer for a Pre-loved Apparel category...
const APPAREL = ["NEW", "NEW_OTHER", "NEW_WITH_DEFECTS",
  "PRE_OWNED_EXCELLENT", "USED_EXCELLENT", "PRE_OWNED_FAIR"];
// ...and for most of the rest of the site: one plain "Used".
const PLAIN = ["NEW", "NEW_OTHER", "SELLER_REFURBISHED", "USED_EXCELLENT",
  "FOR_PARTS_OR_NOT_WORKING"];

describe("nearestCondition", () => {
  it("replaces a refused grade with the closest one, not the first", () => {
    // "New" is first in both lists. Neither answer may be it.
    expect(nearestCondition("USED_GOOD", APPAREL)).toBe("USED_EXCELLENT");
    expect(nearestCondition("USED_GOOD", PLAIN)).toBe("USED_EXCELLENT");
  });

  it("reaches eBay's apparel grades", () => {
    expect(nearestCondition("LIKE_NEW", APPAREL)).toBe("PRE_OWNED_EXCELLENT");
    expect(nearestCondition("USED_ACCEPTABLE", APPAREL)).toBe("PRE_OWNED_FAIR");
  });

  it("never relabels a used item new", () => {
    expect(nearestCondition("USED_GOOD", ["NEW"])).toBeNull();
  });

  it("never quietly calls a new item used", () => {
    expect(nearestCondition("NEW", ["USED_EXCELLENT"])).toBeNull();
    expect(nearestCondition("NEW_WITH_DEFECTS", ["NEW", "NEW_OTHER"])).toBe("NEW_OTHER");
  });

  it("leaves an allowed grade exactly as it is", () => {
    for (const c of APPAREL) expect(nearestCondition(c, APPAREL)).toBe(c);
  });

  it("treats an empty list as 'we could not ask', not 'anything goes'", () => {
    expect(nearestCondition("USED_GOOD", [])).toBe("USED_GOOD");
    expect(nearestCondition("USED_GOOD", null)).toBe("USED_GOOD");
  });

  it("breaks a tie toward the lower grade", () => {
    expect(nearestCondition("PRE_OWNED_EXCELLENT", ["LIKE_NEW", "USED_EXCELLENT"]))
      .toBe("USED_EXCELLENT");
  });

  it("knows every grade it offers in the dropdown", () => {
    // A grade the fallback list can show but the tables don't rank would come
    // back null from every category — unfittable, and blocked forever.
    for (const c of CONDITIONS) {
      expect(nearestCondition(c, ["NEW", "USED_EXCELLENT"])).not.toBeUndefined();
      expect(conditionLabel(c)).toBeTruthy();
    }
  });
});

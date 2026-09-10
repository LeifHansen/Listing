/* Which grade a listing lands on when its category doesn't offer the one it
   has — the browser half of backend/tests/test_condition_follows_the_category.py.

   The editor used to snap an out-of-category condition to the FIRST one eBay
   listed, which is "New" almost everywhere. That swapped a publish error for
   something worse: a worn item advertised as new. These pin the replacement
   as the nearest grade in the same family, and pin the two cases where the
   honest answer is to change nothing. */
import { describe, it, expect } from "vitest";
import {
  CONDITIONS, conditionLabel, conditionSummary, descriptorProblems, descriptorsFor,
  fitDescriptors, hasDescriptors, nearestCondition, sameDescriptors, withDescriptor,
} from "./conditions";

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

/* --- the second step ------------------------------------------------------
   eBay's condition descriptors for a trading card — the browser half of
   backend/tests/test_a_trading_card_is_graded_or_ungraded.py. */

const GRADED = {
  enum: "LIKE_NEW", id: "2750", label: "Graded",
  descriptors: [
    { id: "27501", name: "Professional Grader", required: true, free_text: false,
      cardinality: "SINGLE",
      values: [{ id: "275010", name: "Professional Sports Authenticator (PSA)" }] },
    { id: "27502", name: "Grade", required: true, free_text: false, cardinality: "SINGLE",
      values: [{ id: "275020", name: "10" }, { id: "275021", name: "9.5" }] },
    { id: "27503", name: "Certification Number", required: false, free_text: true,
      max_length: 30, values: [] },
  ],
};
const UNGRADED = {
  enum: "USED_VERY_GOOD", id: "4000", label: "Ungraded",
  descriptors: [
    { id: "40001", name: "Card Condition", required: true, free_text: false,
      cardinality: "SINGLE",
      values: [{ id: "400010", name: "Near Mint or Better" }, { id: "400013", name: "Poor" }] },
  ],
};
const CARDS = [GRADED, UNGRADED];
const GRADED_META = GRADED.descriptors;

describe("descriptorsFor / hasDescriptors", () => {
  it("finds eBay's second step for a condition, and nothing elsewhere", () => {
    expect(descriptorsFor(CARDS, "like_new").map((d) => d.name))
      .toEqual(["Professional Grader", "Grade", "Certification Number"]);
    expect(descriptorsFor(CARDS, "NEW")).toEqual([]);
    expect(descriptorsFor(null, "LIKE_NEW")).toEqual([]);
    expect(hasDescriptors(CARDS)).toBe(true);
    expect(hasDescriptors([{ enum: "NEW", descriptors: [] }])).toBe(false);
  });
});

describe("fitDescriptors", () => {
  it("keeps eBay's order and wording, whatever it was given", () => {
    const fitted = fitDescriptors([
      { id: "27503", text: "12345678" },
      { id: "27502", values: "275020" },
      { id: "27501", values: ["275010"], label: "grader?" },
    ], GRADED_META);
    expect(fitted).toEqual([
      { id: "27501", values: ["275010"], text: "", label: "Professional Grader",
        value_labels: ["Professional Sports Authenticator (PSA)"] },
      { id: "27502", values: ["275020"], text: "", label: "Grade", value_labels: ["10"] },
      { id: "27503", values: [], text: "12345678", label: "Certification Number",
        value_labels: [] },
    ]);
  });

  it("drops what the condition does not take — a PSA grade on an Ungraded card", () => {
    expect(fitDescriptors([{ id: "27501", values: ["275010"] }], UNGRADED.descriptors))
      .toEqual([]);
    expect(fitDescriptors([{ id: "27501", values: ["275010"] }], [])).toEqual([]);
  });

  it("drops a value eBay does not list and clips free text to eBay's length", () => {
    const fitted = fitDescriptors([
      { id: "27502", values: ["999999"] }, { id: "27503", text: "x".repeat(40) },
    ], GRADED_META);
    expect(fitted.map((d) => d.id)).toEqual(["27503"]);
    expect(fitted[0].text).toHaveLength(30);
  });
});

describe("descriptorProblems", () => {
  it("names a required answer that is missing", () => {
    const problems = descriptorProblems([{ id: "27501", values: ["275010"] }], GRADED_META);
    expect(problems).toEqual([{ descriptor: GRADED_META[1], problem: "missing" }]);
  });

  it("names an answer eBay does not offer", () => {
    const problems = descriptorProblems(
      [{ id: "27501", values: ["275010"] }, { id: "27502", values: ["999999"] }], GRADED_META);
    expect(problems.map((p) => [p.descriptor.id, p.problem])).toEqual([["27502", "not_offered"]]);
  });

  it("does not ask for the optional certification number", () => {
    expect(descriptorProblems(
      [{ id: "27501", values: ["275010"] }, { id: "27502", values: ["275020"] }], GRADED_META))
      .toEqual([]);
  });

  it("has nothing to say where there is no second step", () => {
    expect(descriptorProblems([], [])).toEqual([]);
    expect(descriptorProblems(undefined, undefined)).toEqual([]);
  });
});

describe("withDescriptor", () => {
  it("sets one answer and clears it with an empty value", () => {
    const grade = GRADED_META[1];
    const set = withDescriptor([{ id: "27501", values: ["275010"] }], GRADED_META, grade, "275021");
    expect(set.map((d) => [d.id, d.values[0]])).toEqual([["27501", "275010"], ["27502", "275021"]]);
    const cleared = withDescriptor(set, GRADED_META, grade, "");
    expect(cleared.map((d) => d.id)).toEqual(["27501"]);
  });

  it("takes free text for the descriptor that wants it", () => {
    const cert = GRADED_META[2];
    expect(withDescriptor([], GRADED_META, cert, " 998877 ")).toEqual([
      { id: "27503", values: [], text: "998877", label: "Certification Number",
        value_labels: [] }]);
  });
});

describe("sameDescriptors / conditionSummary", () => {
  it("compares ids and text, never labels", () => {
    expect(sameDescriptors(
      [{ id: "27502", values: ["275020"], label: "Grade", value_labels: ["10"] }],
      [{ id: "27502", values: "275020" }])).toBe(true);
    expect(sameDescriptors([{ id: "27502", values: ["275020"] }],
      [{ id: "27502", values: ["275021"] }])).toBe(false);
    expect(sameDescriptors(undefined, [])).toBe(true);
    // ...unless asked to, which is how the editor knows an import's bare ids
    // still want eBay's names.
    expect(sameDescriptors(
      [{ id: "27502", values: ["275020"], label: "Grade", value_labels: ["10"] }],
      [{ id: "27502", values: ["275020"] }], { labels: true })).toBe(false);
  });

  it("says the condition the way a seller would", () => {
    const l = { condition: "LIKE_NEW", condition_descriptors: [
      { id: "27501", values: ["275010"], value_labels: ["Professional Sports Authenticator (PSA)"] },
      { id: "27502", values: ["275020"], value_labels: ["10"] },
      { id: "27503", text: "12345678" },
    ] };
    expect(conditionSummary(l, CARDS))
      .toBe("Graded · Professional Sports Authenticator (PSA) · 10 · #12345678");
    // Without eBay's list the enum's generic name stands in; bare ids show as ids.
    expect(conditionSummary({ condition: "USED_GOOD" })).toBe("Used Good");
    expect(conditionSummary({ condition: "LIKE_NEW",
      condition_descriptors: [{ id: "27502", values: ["275020"] }] })).toBe("Like New · 275020");
    expect(conditionSummary({})).toBe("");
  });
});

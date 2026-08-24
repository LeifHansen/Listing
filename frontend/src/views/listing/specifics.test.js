import { describe, expect, it } from "vitest";
import {
  confirmSpecificRows, reviewAspectCount, specificValues, toggleSpecificValue,
} from "./specifics";

/* eBay's multi-select item specifics — the tick boxes (Features, Style,
   Season...). One aspect, several rows: ticking a value has to ADD one, which
   is exactly what a dropdown-shaped editor could never do. */

const rows = (...pairs) => pairs.map(([name, value, confidence = ""]) =>
  ({ name, value, confidence }));

describe("specificValues", () => {
  it("returns every value held under one aspect name", () => {
    const specs = rows(["Features", "Pockets"], ["Fit", "Slim"],
      ["features", "Lined"]);
    expect(specificValues(specs, "Features")).toEqual(["Pockets", "Lined"]);
  });

  it("skips the empty row a cleared value leaves behind", () => {
    expect(specificValues(rows(["Features", "  "]), "Features")).toEqual([]);
  });
});

describe("toggleSpecificValue", () => {
  it("ticking adds a value instead of replacing the one already there", () => {
    const specs = rows(["Features", "Pockets", "medium"]);
    const next = toggleSpecificValue(specs, "Features", "Lined", true);
    expect(specificValues(next, "Features")).toEqual(["Pockets", "Lined"]);
  });

  it("unticking removes only that value", () => {
    const specs = rows(["Features", "Pockets"], ["Features", "Lined"]);
    const next = toggleSpecificValue(specs, "Features", "Pockets", false);
    expect(specificValues(next, "Features")).toEqual(["Lined"]);
  });

  it("reuses the aspect's empty row rather than accumulating blanks", () => {
    const specs = rows(["Features", ""]);
    const next = toggleSpecificValue(specs, "Features", "Lined", true);
    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({ name: "Features", value: "Lined" });
  });

  it("ticking what's already ticked changes nothing, identity included", () => {
    const specs = rows(["Features", "Pockets"]);
    expect(toggleSpecificValue(specs, "Features", "pockets", true)).toBe(specs);
  });

  it("unticking something that isn't there changes nothing", () => {
    const specs = rows(["Features", "Pockets"]);
    expect(toggleSpecificValue(specs, "Features", "Lined", false)).toBe(specs);
  });

  it("leaves other aspects alone", () => {
    const specs = rows(["Features", "Pockets"], ["Fit", "Slim"]);
    const next = toggleSpecificValue(specs, "Features", "Pockets", false);
    expect(next).toEqual(rows(["Fit", "Slim"]));
  });
});

describe("confirmSpecificRows", () => {
  it("clears the review flag on every row of the aspect", () => {
    const specs = rows(["Features", "Pockets", "medium"],
      ["Features", "Lined", "medium"], ["Fit", "Slim", "medium"]);
    const next = confirmSpecificRows(specs, "Features");
    expect(next.map((s) => s.confidence)).toEqual(["", "", "medium"]);
  });

  it("returns the same list when the aspect has no rows", () => {
    const specs = rows(["Fit", "Slim", "medium"]);
    expect(confirmSpecificRows(specs, "Features")).toBe(specs);
  });
});

describe("reviewAspectCount", () => {
  // The regression this exists for: a multi-select aspect holds one row per
  // ticked value but shows ONE review flag, so counting rows told the seller
  // there were four things to check when there was one.
  it("counts a multi-value aspect once, not once per tick", () => {
    const specs = rows(["Features", "Pockets", "medium"],
      ["Features", "Lined", "medium"], ["Features", "Hooded", "medium"]);
    expect(reviewAspectCount(specs)).toBe(1);
  });

  it("counts each unreviewed aspect separately", () => {
    const specs = rows(["Features", "Pockets", "medium"],
      ["Features", "Lined", "medium"], ["Fit", "Slim", "medium"]);
    expect(reviewAspectCount(specs)).toBe(2);
  });

  it("counts an aspect with any unreviewed tick, however many are confirmed", () => {
    const specs = rows(["Features", "Pockets", "high"],
      ["Features", "Lined", "medium"]);
    expect(reviewAspectCount(specs)).toBe(1);
  });

  it("ignores confirmed, high-confidence and empty rows", () => {
    const specs = rows(["Features", "Pockets", ""], ["Fit", "Slim", "high"],
      ["Style", "", "medium"]);
    expect(reviewAspectCount(specs)).toBe(0);
  });

  it("drops to zero once one click clears the whole group", () => {
    const specs = rows(["Features", "Pockets", "medium"],
      ["Features", "Lined", "medium"]);
    expect(reviewAspectCount(confirmSpecificRows(specs, "Features"))).toBe(0);
  });

  it("survives a missing list", () => {
    expect(reviewAspectCount(undefined)).toBe(0);
  });
});

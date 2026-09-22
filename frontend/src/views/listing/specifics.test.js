import { describe, expect, it } from "vitest";
import {
  specificRowIndex, specificValue, specificValues, toggleSpecificValue,
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

/* Which row an aspect's answer lives in. The bug: a blank row for the aspect
   in FRONT of the row holding its value — left by a cleared field, or by an
   identify pass that returned the name with nothing in it. Everything that
   stopped at the first matching row then called a filled aspect empty, which
   is how "Color: Multi-Color" was reported as a missing required field and
   warned the seller off publishing an edit. */
describe("specificValue", () => {
  it("reads past a blank row to the one holding the answer", () => {
    const specs = rows(["Color", ""], ["Color", "Multi-Color"]);
    expect(specificValue(specs, "Color")).toBe("Multi-Color");
    expect(specificRowIndex(specs, "Color")).toBe(1);
  });

  it("is empty when no row for the aspect holds a value", () => {
    expect(specificValue(rows(["Color", "  "]), "Color")).toBe("");
  });

  it("still points at the aspect's only row when it is blank, so an edit "
     + "lands there instead of appending another", () => {
    expect(specificRowIndex(rows(["Size", "L"], ["Color", ""]), "Color")).toBe(1);
  });

  it("is -1 for an aspect the listing has no row for", () => {
    expect(specificRowIndex(rows(["Size", "L"]), "Color")).toBe(-1);
    expect(specificValue(rows(["Size", "L"]), "Color")).toBe("");
  });

  it("matches the aspect name the way eBay does — case and spacing aside", () => {
    expect(specificValue(rows([" color ", "Red"]), "Color")).toBe("Red");
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

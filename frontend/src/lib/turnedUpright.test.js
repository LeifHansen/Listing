/* What the upload says when the photo pass turned something — and that it
   says nothing when it did not. */
import { describe, expect, it } from "vitest";
import { turnedUprightMessage } from "@/lib/turnedUpright";

describe("turnedUprightMessage", () => {
  it("says nothing when nothing was turned", () => {
    expect(turnedUprightMessage([])).toBeNull();
    expect(turnedUprightMessage(undefined)).toBeNull();
    expect(turnedUprightMessage([
      { file: "img_000.jpg" }, { file: "img_001.jpg", rotated: 0 }, null,
    ])).toBeNull();
  });

  it("counts the turned photos and says how to undo one", () => {
    const one = turnedUprightMessage([{ rotated: 90 }, { bg_error: "x" }]);
    expect(one).toMatch(/^Turned 1 photo upright/);
    expect(one).toMatch(/↻/);
    expect(one).toMatch(/Restore original/);
    expect(turnedUprightMessage([{ rotated: 90 }, { rotated: 270 }, null]))
      .toMatch(/^Turned 2 photos upright/);
  });
});

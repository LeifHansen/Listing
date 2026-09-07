/* The same table as the backend's test_a_price_this_app_chooses_ends_in_99.py.
 *
 * Two copies of a pricing rule is one copy too many, and the only defence
 * against them drifting is that both are checked against the same numbers —
 * including the one the seller reported: $25 lists at $24.99.
 */
import { describe, expect, it } from "vitest";
import { charmPrice } from "./charmPrice";

describe("charmPrice", () => {
  it.each([
    [25, 24.99],          // the reported case, exactly
    [25.0, 24.99],
    [24.99, 24.99],       // already there, and stays put
    [22.5, 22.99],        // nearest, not always down
    [18.75, 18.99],
    [1249.5, 1249.99],
    [1.0, 0.99],
    [0.4, 0.99],          // under the floor comes up to it
  ])("puts %s on %s", (amount, expected) => {
    expect(charmPrice(amount)).toBe(expected);
  });

  it.each([null, undefined, "", "not a price", 0, -5, NaN])(
    "leaves %s alone — it is not a price", (amount) => {
      expect(charmPrice(amount)).toBeNull();
    });

  it("reads the numeric strings the form holds prices as", () => {
    expect(charmPrice("25")).toBe(24.99);
    expect(charmPrice("25.00")).toBe(24.99);
  });
});

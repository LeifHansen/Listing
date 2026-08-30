import { describe, it, expect } from "vitest";
import { priceView } from "./priceLookup";

describe("priceView", () => {
  it("shows the estimate when there is one", () => {
    expect(priceView({ checked: true, suggestion: { price: 40 } }).kind)
      .toBe("estimate");
  });

  it("says the market is empty only when we actually looked", () => {
    const v = priceView({ checked: true, suggestion: null });
    expect(v.kind).toBe("none");
    expect(v.message).toMatch(/No comparable listings/);
  });

  it("does not blame the seller's title for a failed lookup", () => {
    const v = priceView({ checked: false, suggestion: null });
    expect(v.kind).toBe("unavailable");
    expect(v.message).toMatch(/couldn’t check/);
    expect(v.message).not.toMatch(/simpler title/);
    expect(v.message).toMatch(/doesn’t mean/);
  });

  it("a failed request that returned nothing at all is unavailable, not empty", () => {
    // What ShopMode/cards pass when the fetch itself threw.
    expect(priceView({ checked: false }).kind).toBe("unavailable");
  });

  it("defaults to checked so a source without the flag is not called an outage", () => {
    expect(priceView({ suggestion: { price: 1 } }).kind).toBe("estimate");
    expect(priceView({}).kind).toBe("none");
  });
});

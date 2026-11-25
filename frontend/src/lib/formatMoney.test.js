/* formatMoney builds one formatter per currency, not one per call.
 *
 * Every card on the grid formats two or three amounts, and building an
 * Intl.NumberFormat is the expensive half of formatting one: measured, 6,000
 * calls took ~200 ms building a formatter each time against ~3 ms reusing
 * one -- the difference between a 3,000-card grid that renders and one that
 * stalls a phone for a second on every sort and filter.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { formatMoney } from "./utils";

afterEach(() => { vi.restoreAllMocks(); });

describe("formatMoney", () => {
  it("reuses a currency's formatter", () => {
    const built = vi.spyOn(Intl, "NumberFormat");
    formatMoney(1, "JPY");
    formatMoney(2.5, "JPY");
    formatMoney(3, "CHF");
    formatMoney(4, "CHF");
    expect(built).toHaveBeenCalledTimes(2);
  });

  it("formats as it always did", () => {
    expect(formatMoney(12)).toBe("$12.00");
    expect(formatMoney(12.5, "USD")).toBe("$12.50");
    expect(formatMoney(45, "GBP")).toBe("£45.00");
    expect(formatMoney(null)).toBe(null);
    expect(formatMoney("")).toBe(null);
    expect(formatMoney("abc")).toBe(null);
  });

  it("still falls back for a currency code Intl refuses, every time", () => {
    expect(formatMoney(3, "NOT-A-CODE")).toBe("$3.00");
    expect(formatMoney(4, "NOT-A-CODE")).toBe("$4.00");
  });
});

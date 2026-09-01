/* The console's pure rules: dash-not-zero, labeled estimates, stable merges. */
import { describe, expect, it } from "vitest";
import {
  tileValue, tileSub, salesSubline, actionLabel, relTime, signedTokens,
  mergeRows,
} from "@/lib/adminView";

describe("tileValue / tileSub", () => {
  const ready = { kind: "ready", data: { n: 0 } };

  it("renders a dash while loading and during an outage — never a zero", () => {
    expect(tileValue({ kind: "loading" }, (d) => d.n)).toBe("—");
    expect(tileValue({ kind: "unavailable" }, (d) => d.n)).toBe("—");
  });

  it("renders a real zero when the read succeeded", () => {
    expect(tileValue(ready, (d) => d.n)).toBe(0);
  });

  it("a null inside loaded data is also a dash, not a 0", () => {
    expect(tileValue({ kind: "ready", data: { n: null } }, (d) => d.n)).toBe("—");
  });

  it("the sub-line admits an outage and stays quiet while loading", () => {
    expect(tileSub({ kind: "unavailable" })).toBe("we couldn’t check");
    expect(tileSub({ kind: "loading" })).toBe("");
    expect(tileSub(ready, (d) => `${d.n} things`)).toBe("0 things");
    expect(tileSub(ready, "static")).toBe("static");
  });
});

describe("salesSubline", () => {
  it("labels estimates and currency mixes instead of laundering them", () => {
    expect(salesSubline({ count: 3, approx: 1, mixed_currency: true, undated: 2 }))
      .toBe("3 sales · 1 at the asking price · more than one currency · 2 undated");
    expect(salesSubline({ count: 1, approx: 0, mixed_currency: false, undated: 0 }))
      .toBe("1 sale");
  });
});

describe("labels and formats", () => {
  it("maps known audit actions and falls through readably", () => {
    expect(actionLabel("grant_tokens")).toBe("Granted tokens");
    expect(actionLabel("some_new_action")).toBe("some new action");
  });

  it("signs token amounts with a real minus sign", () => {
    expect(signedTokens(25)).toBe("+25");
    expect(signedTokens(-12)).toBe("−12");
  });

  it("relative time survives garbage", () => {
    const now = Date.parse("2026-08-31T12:00:00Z");
    expect(relTime("2026-08-31T11:59:40Z", now)).toBe("just now");
    expect(relTime("2026-08-31T09:00:00Z", now)).toBe("3h ago");
    expect(relTime("not a date", now)).toBe("");
    expect(relTime(null, now)).toBe("");
  });
});

describe("mergeRows", () => {
  it("appends without repeating a row that moved across the page edge", () => {
    const first = [{ id: "a" }, { id: "b" }];
    const second = [{ id: "b" }, { id: "c" }];
    expect(mergeRows(first, second).map((r) => r.id)).toEqual(["a", "b", "c"]);
  });
});

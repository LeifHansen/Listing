/* A dismissal has to survive a reload — and must not eat the storage quota.
 *
 * "Suggested actions" is rebuilt from scratch on every load, so waving a row
 * away only means something if the decision outlives the page. It is kept in
 * this browser, which puts it in the same few megabytes as the theme, the
 * sold-range picker and the id of a running bulk batch — and a browser out of
 * quota does not drop the oldest key, it throws on the next write. So the
 * list is bounded, and every path through this module survives storage that
 * refuses to answer at all.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { dismiss, readDismissed, recKey, restoreAll, withoutDismissed } from "./dismissedRecs";

const rec = (id, type = "specifics") => ({ listing_id: id, type });

describe("dismissed suggestions", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

  it("remembers a dismissal across a reload", () => {
    dismiss([], rec("a"));
    expect(readDismissed()).toEqual([recKey(rec("a"))]);
  });

  it("is per listing AND per kind of advice", () => {
    // Waving away "lower the price" on one item must not silence its photo
    // suggestion, nor every other listing's price suggestion.
    const list = dismiss([], rec("a", "lower_price"));
    const recs = [rec("a", "lower_price"), rec("a", "photos"), rec("b", "lower_price")];
    expect(withoutDismissed(recs, list)).toEqual([recs[1], recs[2]]);
  });

  it("counts one listing's suggestion once, however often it is dismissed", () => {
    let list = dismiss([], rec("a"));
    list = dismiss(list, rec("a"));
    expect(list).toEqual([recKey(rec("a"))]);
  });

  it("keeps the newest and drops the oldest rather than growing forever", () => {
    let list = [];
    for (let i = 0; i < 320; i += 1) list = dismiss(list, rec(`item-${i}`));
    expect(list).toHaveLength(300);
    expect(list.at(-1)).toBe(recKey(rec("item-319")));
    expect(list).not.toContain(recKey(rec("item-0")));
  });

  it("brings everything back in one go", () => {
    const list = dismiss(dismiss([], rec("a")), rec("b"));
    expect(restoreAll()).toEqual([]);
    expect(readDismissed()).toEqual([]);
    expect(withoutDismissed([rec("a"), rec("b")], readDismissed())).toHaveLength(2);
    expect(list).toHaveLength(2);   // the caller's copy is never mutated
  });

  it("treats storage it cannot read as nothing dismissed", () => {
    // Safari with site data blocked THROWS rather than returning null, and a
    // suggestion list that cannot be read must not become a blank screen.
    localStorage.setItem("thryft-dismissed-recs", "{not json");
    expect(readDismissed()).toEqual([]);
  });

  it("survives storage that refuses the write", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(dismiss([], rec("a"))).toEqual([recKey(rec("a"))]);
  });
});

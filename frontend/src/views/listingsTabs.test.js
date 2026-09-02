/* Where a listing shows up in the pipeline once it has sold.
 *
 * The defect these pin down: a sold listing stayed among the things the
 * seller could still act on. It sat in the everything-tab beside the live
 * ones, its card offered "Relist", and opening it gave the full publish
 * workflow — a finished sale reading "Ready to publish", one tap from
 * re-listing the item that had already gone.
 *
 * A sale is now archived: sold and ended share the Inactive tab, and sold is
 * hidden everywhere else.
 */
import { describe, expect, it } from "vitest";
import { TABS, STALE_TABS, inTab } from "./ListingsView";
import { ARCHIVED_STATUSES, recentListings } from "@/lib/listingsView";

const tab = (id) => TABS.find((t) => t.id === id);
const item = (status) => ({ id: status, status });

describe("the pipeline's tabs", () => {
  it("archives a sold listing under Inactive", () => {
    expect(inTab(tab("inactive"), item("sold"))).toBe(true);
  });

  it("keeps ended-without-selling in the same archive", () => {
    expect(inTab(tab("inactive"), item("ended"))).toBe(true);
  });

  it("no longer has a tab of its own for sold", () => {
    expect(TABS.map((t) => t.id)).not.toContain("sold");
  });

  it("keeps a sold listing out of Active", () => {
    expect(inTab(tab("active"), item("sold"))).toBe(false);
    expect(inTab(tab("active"), item("published"))).toBe(true);
    expect(inTab(tab("active"), item("live"))).toBe(true);
  });

  it("hides a sold listing from the everything-tab too", () => {
    // The whole point of "hide": All is where a seller scans the store, and
    // a finished sale in that grid is the thing they can't act on.
    expect(inTab(tab("all"), item("sold"))).toBe(false);
  });

  it("still shows everything else in the everything-tab", () => {
    for (const status of ["draft", "dry_run", "published", "live", "unlisted", "ended"]) {
      expect(inTab(tab("all"), item(status))).toBe(true);
    }
  });

  it("leaves drafts and finds where they were", () => {
    expect(inTab(tab("finds"), item("unlisted"))).toBe(true);
    expect(inTab(tab("finds"), item("draft"))).toBe(false);
  });
});

describe("a remembered tab that no longer exists", () => {
  it("sends the old Sold tab to the archive it was folded into", () => {
    // The selection is remembered across visits: a seller whose last visit
    // ended on Sold must land on Inactive, not on a blank screen.
    expect(STALE_TABS.sold).toBe("inactive");
  });

  it("still sends the pre-strip Drafts tab to Active", () => {
    expect(STALE_TABS.drafts).toBe("active");
  });

  it("names only tabs that exist", () => {
    const ids = TABS.map((t) => t.id);
    for (const dest of Object.values(STALE_TABS)) expect(ids).toContain(dest);
  });
});

describe("counting", () => {
  const items = [
    item("published"), item("live"), item("draft"),
    item("ended"), item("sold"), item("sold"), item("unlisted"),
  ];
  const count = (id) => items.filter((i) => inTab(tab(id), i)).length;

  it("counts each sold listing once, in the archive", () => {
    expect(count("inactive")).toBe(3);   // 1 ended + 2 sold
    expect(count("active")).toBe(2);
  });

  it("leaves the sold ones out of the All count", () => {
    expect(count("all")).toBe(items.length - 2);
  });
});

describe("the dashboard's strip and the Sell screen's tabs", () => {
  // These two are the halves of the reported defect: a sold item left the
  // tabs and stayed on the dashboard. They now subtract the same list, so
  // adding a status to one screen cannot forget the other.
  it("hide the same statuses", () => {
    for (const status of ARCHIVED_STATUSES) {
      expect(inTab(tab("all"), item(status))).toBe(false);
      expect(recentListings([{ id: status, status, updated_at: "2026-03-01" }]))
        .toEqual([]);
    }
  });

  it("keeps the archive itself showing every one of them", () => {
    // Hidden everywhere is not the goal — hidden everywhere BUT Inactive is.
    for (const status of ARCHIVED_STATUSES) {
      expect(inTab(tab("inactive"), item(status))).toBe(true);
    }
  });
});

/* Where a listing shows up in the pipeline once it has finished.
 *
 * Two reports, pulling opposite ways, and both have to hold.
 *
 * The first: a sold listing stayed among the things the seller could still
 * act on. It sat in the everything-tab beside the live ones, its card offered
 * "Relist", and opening it gave the full publish workflow — a finished sale
 * reading "Ready to publish", one tap from re-listing the item that had
 * already gone. The fix at the time was to archive anything finished under
 * Inactive and subtract it from All.
 *
 * The second was a screenshot of the tab badges that subtraction produced:
 * "Active 355 · Inactive 11 · All 355". A tab called All that holds less than
 * Active plus Inactive reads as a miscount, and it was one — the number was
 * right for what the tab showed, and the tab was not what it said it was.
 *
 * So the archive is where finished listings live (Inactive, and never Active
 * or the dashboard strip), and All is the whole store: it holds them too,
 * after everything still in play, and its count is the sum. What keeps the
 * first report from coming back is the card, not the tab — a sold card says
 * "View sale", an ended one "Relist", and neither looks publishable.
 */
import { describe, expect, it } from "vitest";
import { TABS, STALE_TABS, inTab } from "./ListingsView";
import { ARCHIVED_STATUSES, gridOrder, recentListings } from "@/lib/listingsView";

const tab = (id) => TABS.find((t) => t.id === id);
const item = (status) => ({ id: status, status });

describe("the pipeline's tabs", () => {
  it("archives a sold listing under Inactive", () => {
    expect(inTab(tab("inactive"), item("sold"))).toBe(true);
  });

  it("keeps an ended one there too, for as long as it is here", () => {
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

  it("keeps an ended one out of Active too", () => {
    expect(inTab(tab("active"), item("ended"))).toBe(false);
  });

  it("shows the whole store under All, the archive included", () => {
    // All is the one tab whose name is a claim about the count. Leaving the
    // archive out of it is what put "All 355" beside "Inactive 11".
    for (const status of [
      "draft", "dry_run", "published", "live", "unlisted", "sold", "ended",
    ]) {
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

  it("counts each finished listing once, in the archive", () => {
    expect(count("inactive")).toBe(3);   // 2 sold + 1 ended
    expect(count("active")).toBe(2);
  });

  it("makes All add up", () => {
    // The report: Active 355, Inactive 11, All 355. Every listing on the
    // page is in All, so the sliced tabs — plus the drafts, which have a
    // strip of their own rather than a tab — sum to it.
    expect(count("all")).toBe(items.length);
    const drafts = items.filter((i) => ["draft", "dry_run"].includes(i.status)).length;
    expect(count("active") + count("finds") + count("inactive") + drafts)
      .toBe(count("all"));
  });

  it("puts no listing in two of the sliced tabs", () => {
    // Without which the sum above would count something twice.
    for (const i of items) {
      const homes = ["active", "finds", "inactive"].filter((id) => inTab(tab(id), i));
      expect(homes.length).toBeLessThanOrEqual(1);
    }
  });
});

describe("the dashboard's strip and the Sell screen's grid", () => {
  // The two readers of ARCHIVED_STATUSES. A sale or an ending is the last
  // thing to touch a row, so by recency it would head both: the strip drops
  // it (four cards of things to carry on with), and All keeps it but after
  // the live ones. Adding a status to the list moves it on both screens.
  it("both move a finished listing out of the way of the live ones", () => {
    for (const status of ARCHIVED_STATUSES) {
      const finished = { id: status, status, updated_at: "2026-03-02" };
      const live = { id: "live", status: "live", updated_at: "2026-03-01" };
      expect(recentListings([finished, live])).toEqual([live]);
      expect(gridOrder([finished, live])).toEqual([live, finished]);
    }
  });

  it("keeps the archive itself showing every one of them", () => {
    // Hidden from Active is not the goal — filed under Inactive is.
    for (const status of ARCHIVED_STATUSES) {
      expect(inTab(tab("inactive"), item(status))).toBe(true);
    }
  });
});

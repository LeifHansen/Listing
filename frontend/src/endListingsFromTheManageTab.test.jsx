/* End listings, from the Manage tab: the card's End, over the ticks.
 *
 * Under More, because it is the one action on the bar with no way back. The
 * confirm says what becomes of each card — a listing made here is kept under
 * Inactive for the grace period, a copy the store sync made is removed — and
 * only then is anything sent: exactly the ticked ids. */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { job, live, mountManage } from "@/test/manageHarness";

describe("End listings from the Manage tab", () => {
  let m;
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => { await m.unmount(); });

  it("asks first, saying what happens to each card, then ends exactly the ticks", async () => {
    const run = job("end-1", { changed: 2, skipped: 0, failed: 0, removed: 1,
      results: { changed: [], skipped: [], failed: [] } },
    (body, state) => { state.listings = state.listings.filter((l) => !body.listing_ids.includes(l.id)); });
    m = await mountManage(
      [
        // Made here: kept under Inactive.
        { ...live("mine", "Made here"), id: "mine" },
        // A mirror of an eBay listing the sync made: removed.
        { ...live("ebay-123", "A mirror", { images: [] }), id: "ebay-123" },
        live("c", "Left alone"),
      ],
      { "/api/ebay/end-listings": run.start, "/api/bulk/status/end-1": run.status });
    const [a, b] = m.ticks();
    await m.click(a);
    await m.click(b);

    await m.openMore();
    await m.click(m.menuItem("End listings"));
    await m.settle();
    expect(m.text()).toContain("End 2 listings on eBay?");
    expect(m.text()).toMatch(/1 move to Inactive/);
    expect(m.text()).toMatch(/1 card is removed/);
    expect(m.state.asked["/api/ebay/end-listings"]).toBeUndefined();

    await m.click(m.button("End 2 listings"));
    await m.settle(20);
    expect(m.state.asked["/api/ebay/end-listings"].listing_ids).toEqual(["mine", "ebay-123"]);
    expect(m.text()).toContain("Ended 2 listings");
    expect(m.text()).toContain("Left alone");
    expect(m.text()).not.toContain("A mirror");
  });

  it("sends nothing when the seller backs out", async () => {
    m = await mountManage([live("a", "Keep me")], {
      "/api/ebay/end-listings": () => { throw new Error("must not be called"); },
    });
    await m.click(m.ticks()[0]);
    await m.openMore();
    await m.click(m.menuItem("End listings"));
    await m.settle();
    await m.click(m.button("Cancel"));
    await m.settle();
    expect(m.state.asked["/api/ebay/end-listings"]).toBeUndefined();
    expect(m.text()).toContain("Keep me");
  });
});

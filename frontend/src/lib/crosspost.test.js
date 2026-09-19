/* What the crosspost will and won't take, before anything is sent. */
import { describe, expect, it } from "vitest";
import { candidates, crosspostSkipReason, tally } from "./crosspost";

const SETTINGS = { selected: { shipping_profile_id: "7", return_policy_id: "8", readiness_state_id: "9" } };
const live = (id, over = {}) => ({
  id, status: "published",
  listing: { title: `Item ${id}`, description: "Nice.", price: 20, quantity: 1,
    images: ["a.jpg"], marketplaces: { ebay: { status: "published" } },
    etsy: { taxonomy_id: 1, who_made: "someone_else", when_made: "1990s" }, ...over },
});

describe("what is left out, and why", () => {
  it("skips a listing already on Etsy, an auction, variations, and anything not live", () => {
    expect(crosspostSkipReason(live("a", { marketplaces: { etsy: { status: "draft" } } })))
      .toMatch(/already on etsy/i);
    expect(crosspostSkipReason(live("b", { listing_format: "AUCTION" }))).toMatch(/auction/i);
    expect(crosspostSkipReason(live("c", { has_variations: true }))).toMatch(/variations/i);
    expect(crosspostSkipReason({ ...live("d"), status: "draft" })).toMatch(/not live/i);
    expect(crosspostSkipReason(live("e"))).toBe("");
  });
});

describe("the rows", () => {
  it("say ready, what is needed, or why skipped — and add up", () => {
    const rows = candidates([
      live("ready"),
      live("needs", { etsy: {} }),
      live("skip", { listing_format: "AUCTION" }),
    ], { etsySettings: SETTINGS });
    expect(rows.map((r) => r.ready)).toEqual([true, false, false]);
    expect(rows[1].blockers.map((b) => b.key)).toEqual(
      expect.arrayContaining(["etsy_taxonomy", "etsy_attribution"]));
    expect(rows[2].skip).toMatch(/auction/i);
    expect(rows[2].blockers).toEqual([]);
    expect(tally(rows)).toEqual({ ready: 1, needing: 1, skipped: 1 });
  });

  it("leaves the profile rules to the server while the shop's settings are unknown", () => {
    const [row] = candidates([live("x")], { etsySettings: null });
    expect(row.ready).toBe(true);
  });
});

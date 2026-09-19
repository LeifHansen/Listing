/* What Etsy is allowed to call "blocking" — and how it joins eBay's list.
 *
 * The defect these pin down: the Publish button read eBay's rules alone, so
 * an Etsy-only publish with no category, no attribution and no shipping
 * profile said "Ready to publish", fired, and was refused by the server for
 * all three. The Etsy rules now live beside eBay's, mirror the server's
 * error-level checks, and every publish surface asks for both lists at once.
 */
import { describe, expect, it } from "vitest";
import {
  blockerHeadline, blockersFor, etsyBlockers, etsyTitle, isVintage, marketNames,
  needsProductionPartner,
} from "./blockers";

const SETTINGS = {
  shipping_profiles: [{ id: "7", name: "Standard" }],
  return_policies: [{ id: "8", name: "Returns" }],
  readiness_states: [{ id: "9", name: "1–3 days" }],
  selected: { shipping_profile_id: "7", return_policy_id: "8", readiness_state_id: "9" },
};

function draft(over = {}) {
  return {
    title: "Vintage 1990s Levi's 501 Jeans 32x30",
    description: "<p>Great pair.</p>",
    price: 45, quantity: 1, listing_format: "FIXED_PRICE",
    images: ["img_000.jpg"],
    etsy: { taxonomy_id: 1234, who_made: "someone_else", when_made: "1990s" },
    ...over,
  };
}

const keys = (l, opts) => etsyBlockers(l, { settings: SETTINGS, ...opts }).map((b) => b.key);

describe("a listing Etsy would accept", () => {
  it("has nothing blocking it", () => {
    expect(etsyBlockers(draft(), { settings: SETTINGS })).toEqual([]);
  });

  it("is not blocked by eBay-only fields", () => {
    expect(keys(draft({ category_id: "", package_weight_lb: 0 }))).toEqual([]);
  });
});

describe("what Etsy refuses", () => {
  it("an auction", () => {
    expect(keys(draft({ listing_format: "AUCTION" }))).toContain("format");
  });

  it("a listing with variations", () => {
    expect(keys(draft({ has_variations: true }))).toContain("variations");
  });

  it("a missing description — which eBay never minds", () => {
    expect(keys(draft({ description: "<p> </p>" }))).toContain("description");
  });

  it("a price under Etsy's floor", () => {
    expect(keys(draft({ price: 0.1 }))).toContain("price");
  });

  it("a title that tidies to nothing", () => {
    expect(keys(draft({ title: "$^`" }))).toContain("title");
    expect(keys(draft({ title: "" }))).toContain("title");
  });

  it("no Etsy category, and no answer to who or when", () => {
    expect(keys(draft({ etsy: {} }))).toEqual(
      expect.arrayContaining(["etsy_taxonomy", "etsy_attribution"]));
  });

  it("something someone else made recently — Etsy's production-partner case", () => {
    const recent = draft({ etsy: { taxonomy_id: 1, who_made: "someone_else", when_made: "2010_2019" } });
    const partner = etsyBlockers(recent, { settings: SETTINGS }).find((b) => b.key === "partner");
    expect(partner).toBeTruthy();
    expect(partner.target).toBe("etsy_attribution");
    expect(keys(draft({ etsy: { taxonomy_id: 1, who_made: "someone_else", when_made: "2010_2019", is_supply: true } })))
      .not.toContain("partner");
    expect(keys(draft({ etsy: { taxonomy_id: 1, who_made: "i_did", when_made: "2010_2019" } })))
      .not.toContain("partner");
  });
});

describe("the three profiles", () => {
  it("are judged against the account defaults, and a listing override wins", () => {
    const none = { ...SETTINGS, selected: {} };
    expect(etsyBlockers(draft(), { settings: none }).map((b) => b.key)).toEqual(
      ["etsy_shipping_profile", "etsy_readiness_state", "etsy_return_policy"]);
    const overridden = draft({ etsy: { ...draft().etsy, shipping_profile_id: "1",
      readiness_state_id: "2", return_policy_id: "3" } });
    expect(etsyBlockers(overridden, { settings: none })).toEqual([]);
  });

  it("are skipped, not guessed, when nobody has loaded the shop's settings", () => {
    expect(etsyBlockers(draft(), { settings: null })).toEqual([]);
  });

  it("let a draft leave the return policy and the photo for later", () => {
    const noReturns = { ...SETTINGS, selected: { ...SETTINGS.selected, return_policy_id: "" } };
    expect(etsyBlockers(draft({ images: [] }), { settings: noReturns, mode: "draft" })).toEqual([]);
    expect(etsyBlockers(draft({ images: [] }), { settings: noReturns, mode: "live" }).map((b) => b.key))
      .toEqual(["photos", "etsy_return_policy"]);
  });

  it("treat an id that is not a number as missing", () => {
    const stray = { ...SETTINGS, selected: { ...SETTINGS.selected, readiness_state_id: "fast" } };
    expect(etsyBlockers(draft(), { settings: stray }).map((b) => b.key)).toEqual(["etsy_readiness_state"]);
  });
});

describe("one list for every marketplace a publish goes to", () => {
  const ebayReady = draft({ condition: "USED_GOOD", category_id: "15709", package_weight_lb: 2 });

  it("is eBay's list alone on the legacy path", () => {
    expect(blockersFor(ebayReady, null, { etsySettings: SETTINGS })).toEqual([]);
    expect(blockersFor({ ...ebayReady, etsy: {} }, null, { etsySettings: SETTINGS })).toEqual([]);
  });

  it("adds Etsy's asks when Etsy is a target, without repeating a shared field", () => {
    const both = blockersFor({ ...ebayReady, title: "", etsy: {} }, ["ebay", "etsy"],
      { etsySettings: SETTINGS });
    expect(both.filter((b) => b.key === "title")).toHaveLength(1);
    expect(both.map((b) => b.key)).toEqual(
      expect.arrayContaining(["title", "etsy_taxonomy", "etsy_attribution"]));
  });

  it("does not hold an Etsy-only publish to eBay's category or package weight", () => {
    const etsyOnly = blockersFor(draft({ category_id: "", package_weight_lb: 0 }), ["etsy"],
      { etsySettings: SETTINGS });
    expect(etsyOnly).toEqual([]);
  });
});

describe("the words", () => {
  it("name the marketplaces the publish is going to", () => {
    expect(marketNames(null)).toBe("eBay");
    expect(marketNames(["etsy"])).toBe("Etsy");
    expect(marketNames(["ebay", "etsy"])).toBe("eBay and Etsy");
    expect(blockerHeadline([{ key: "a" }, { key: "b" }, { key: "c" }], ["etsy"]))
      .toBe("3 fields Etsy won't accept");
    expect(blockerHeadline([{ key: "a" }], null)).toBe("1 field eBay won't accept");
    expect(blockerHeadline([], ["etsy"])).toBe("");
  });
});

describe("the tidied title mirrors the server", () => {
  it("tidies exactly as mapping_etsy.clean_title does", () => {
    expect(etsyTitle("$$ NIKE AIR MAX 90 VTG LEVI'S NWT & rare & mint"))
      .toBe("NIKE AIR MAX 90 Vtg Levi's Nwt & rare  mint".replace("  ", " "));
    expect(etsyTitle("*** Vintage mug")).toBe("Vintage mug");
    expect(etsyTitle("Mug $12 ^ `rare`")).toBe("Mug 12 rare");
    expect(etsyTitle("$^`")).toBe("");
    const clean = "Vintage 1990s Levi's 501 Jeans 32x30";
    expect(etsyTitle(clean)).toBe(clean);
  });

  it("knows what vintage means against the calendar", () => {
    expect(isVintage("1990s", 2026)).toBe(true);
    expect(isVintage("2000_2006", 2026)).toBe(true);
    expect(isVintage("2007_2009", 2026)).toBe(false);
    expect(isVintage("2007_2009", 2029)).toBe(true);
    expect(isVintage("made_to_order", 2026)).toBe(false);
    expect(needsProductionPartner({ who_made: "someone_else", when_made: "2010_2019" }, 2026)).toBe(true);
    expect(needsProductionPartner({ who_made: "someone_else", when_made: "1990s" }, 2026)).toBe(false);
  });
});

/**
 * "No listings yet" is a claim about the seller's account.
 *
 * A failed /api/listings used to produce it. The cache started at `items: []`,
 * the catch set `loaded: true` and left the items alone, and the view's only
 * question was whether the list was empty — so a database blip rendered as an
 * empty store, complete with a button to create a first listing, for a seller
 * who might have four hundred. The error itself was a toast that vanished.
 */
import { describe, expect, it } from "vitest";

import { listingsView, storeTotal } from "./listingsView.js";

const USER = { id: "u1" };

describe("what the listings area may claim", () => {
  it("shows skeletons on a first load", () => {
    expect(listingsView({ loading: true, loaded: false, user: USER }).kind)
      .toBe("loading");
  });

  it("does not report a failed load as an empty store", () => {
    // The finding.
    const v = listingsView({ loaded: true, user: USER, error: "503", count: 0 });
    expect(v.kind).toBe("unavailable");
    expect(v.message.toLowerCase()).not.toContain("no listings yet");
  });

  it("still says empty when the store really was read and is empty", () => {
    expect(listingsView({ loaded: true, user: USER, count: 0 }).kind).toBe("empty");
  });

  it("keeps showing the store when a background refresh fails", () => {
    // Replacing a good list with an error card would lose the seller's place
    // over a blip they could otherwise ignore.
    expect(listingsView({ loaded: true, user: USER, error: "503", count: 12 }).kind)
      .toBe("list");
  });

  it("puts the missing database ahead of everything else", () => {
    expect(listingsView({ loaded: true, dbConfigured: false, user: USER, error: "x" }).kind)
      .toBe("no-db");
  });

  it("asks a logged-out visitor to log in rather than reporting on an account", () => {
    expect(listingsView({ loaded: true, user: null, error: "x" }).kind)
      .toBe("logged-out");
  });
});

describe("a page that is not the whole store", () => {
  it("says so rather than reading as complete", () => {
    const v = listingsView({ loaded: true, user: USER, count: 3000,
                             truncated: true });
    expect(v.kind).toBe("list");
    expect(v.notice).toMatch(/more than we can show/i);
    // The bulk checkboxes run over the page, not the store.
    expect(v.notice.toLowerCase()).toContain("bulk actions");
  });

  it("stays quiet when the page IS the whole store", () => {
    expect(listingsView({ loaded: true, user: USER, count: 12 }).notice)
      .toBe("");
  });

  it("claims no total it did not count", () => {
    const v = listingsView({ loaded: true, user: USER, count: 3000,
                             truncated: true });
    // "3000 of 4127" would be an invented number: the endpoint asks for one
    // row more than it returns, so it knows there are more and not how many.
    expect(v.notice).not.toMatch(/of \d+/);
  });
});

describe("a store total on the dashboard", () => {
  // The tiles above the listings area are counted off the same page this
  // module is about, so they inherit the same problem one level up: during an
  // outage they all count zero and each states it as a fact. A seller acts on
  // these -- nothing live is a reason to list something, nothing sold this
  // week is a reason to cut prices -- so a number nobody measured is worse
  // here than a blank.
  it("refuses to report a number it could not measure", () => {
    const t = storeTotal("unavailable", 0, "everything currently live");
    expect(t.value).toBe("—");
    expect(t.sub).toMatch(/couldn’t check/);
  });

  it("does not invent an outage over a store that is really empty", () => {
    // The other direction. A seller with nothing listed has genuinely nothing
    // live, and a dash where a zero belongs is its own kind of lie.
    expect(storeTotal("empty", 0, "everything currently live"))
      .toEqual({ value: 0, sub: "everything currently live" });
  });

  it("passes a real count through untouched", () => {
    expect(storeTotal("list", 12, "$430.00 listed"))
      .toEqual({ value: 12, sub: "$430.00 listed" });
  });

  it("works for a total that is already formatted, not just a count", () => {
    // The Sold tile passes a money string, not a number.
    expect(storeTotal("unavailable", "$412.00", "in the last 7 days").value)
      .toBe("—");
    expect(storeTotal("list", "$412.00", "in the last 7 days").value)
      .toBe("$412.00");
  });
});

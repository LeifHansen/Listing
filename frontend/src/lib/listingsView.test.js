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
    // Unchanged in substance, narrowed in scope. The endpoint asks for one
    // row more than it returns, so `truncated` costs nothing and arrives on
    // every load; the COUNT only runs for the seller past the cap and can
    // fail on its own. With no `total`, "3000 of 4127" would still be an
    // invented number.
    expect(v.notice).not.toMatch(/of \d+/);
  });

  it("says what the page was cut from when the store was counted", () => {
    const v = listingsView({ loaded: true, user: USER, count: 3000,
                             truncated: true, total: 4127 });
    expect(v.notice).toContain("3,000 of 4,127");
    // Still the part that matters most: the checkboxes run over the page.
    expect(v.notice.toLowerCase()).toContain("bulk actions");
  });

  it("ignores a total that cannot be true", () => {
    // A total smaller than the page it is describing is not a total; the
    // honest notice is the one that names no number at all.
    const v = listingsView({ loaded: true, user: USER, count: 3000,
                             truncated: true, total: 12 });
    expect(v.notice).not.toMatch(/of \d+/);
    expect(v.notice).toMatch(/more than we can show/i);
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

describe("the sentence shown when the store could not be read", () => {
  // Whatever `api()` throws is already a complete sentence written for the
  // seller: it writes one for a timeout, one for a dropped connection, and
  // since the P2-07 pass the server writes one too. Wrapping that in a second
  // sentence produced the same words twice around a status code —
  //
  //   We couldn’t load your listings ((503) We couldn’t load your listings
  //   just now.). This doesn’t mean you don’t have any — try again in a moment.
  //
  // — which is the shape P2-07 exists to remove, arrived at from the other
  // end: the server's copy got better and the client kept dressing it up.
  const SERVER = "We couldn’t load your listings just now — this doesn’t "
    + "mean you don’t have any. Try again in a moment.";

  it("shows what the seller was told, once", () => {
    const v = listingsView({ loaded: true, user: USER, count: 0, error: SERVER });
    expect(v.kind).toBe("unavailable");
    expect(v.message).toBe(SERVER);
  });

  it("does not say the same thing twice", () => {
    const v = listingsView({ loaded: true, user: USER, count: 0, error: SERVER });
    expect(v.message.match(/couldn’t load your listings/gi)).toHaveLength(1);
  });

  it("still has something to say when the failure came with no words", () => {
    const v = listingsView({ loaded: true, user: USER, count: 0, error: " " });
    expect(v.kind).toBe("unavailable");
    expect(v.message).toMatch(/doesn’t mean you don’t have any/);
  });

  it("leaves a store that is on screen alone", () => {
    // Unchanged, and the reason this check is `error && !count`: a refresh
    // that fails while the previous answer is still up should not replace a
    // real store with an error card.
    expect(listingsView({ loaded: true, user: USER, count: 4, error: SERVER }).kind)
      .toBe("list");
  });
});

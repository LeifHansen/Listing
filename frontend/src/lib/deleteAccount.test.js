/**
 * The one warning a delete dialog must never lose.
 *
 * Deleting the account removes Thryft Shop's copy of a listing. It does NOT
 * end the listing on eBay: anything already published stays live under the
 * seller's own eBay account and keeps selling — and keeps taking orders the
 * seller can no longer see from here. So the dialog warns, and
 * /api/account/summary carries `counted` precisely so it can warn even when
 * the numbers are unreadable. Its docstring: "Silently showing '0 live
 * listings' would suppress exactly the warning this endpoint exists to" give.
 *
 * The client then defeated it. When the summary call failed outright, the
 * catch set `{}` — so `counted` was UNDEFINED, not false, the warning's
 * `counted === false` test did not fire, `live_listings` was absent, and the
 * seller was shown "this permanently erases your account and everything saved
 * to it" with no mention of the listings still selling on eBay. The guard was
 * written for the server saying "I couldn't count" and bypassed by the client
 * failing to ask at all.
 */
import { describe, expect, it } from "vitest";

import { deleteAccountNotice } from "./deleteAccount.js";

const COUNTED = { counted: true, listings: 12, live_listings: 4,
                  ebay_connected: true };

describe("what the delete dialog may claim", () => {
  it("names the counts when they were actually read", () => {
    const n = deleteAccountNotice(COUNTED);
    expect(n.listings).toBe(12);
    expect(n.warning).toEqual({ kind: "live", count: 4 });
  });

  it("warns generically when the server could not count", () => {
    const n = deleteAccountNotice({ counted: false });
    expect(n.listings).toBe(null);
    expect(n.warning).toEqual({ kind: "unknown" });
  });

  it("warns generically when the summary could not be fetched at all", () => {
    // The finding. An empty object is what the failed fetch produced, and it
    // satisfied neither branch of the warning's condition.
    expect(deleteAccountNotice({}).warning).toEqual({ kind: "unknown" });
    expect(deleteAccountNotice({ counted: undefined }).warning)
      .toEqual({ kind: "unknown" });
  });

  it("stays silent before anything has been asked", () => {
    // No dialog content yet — not a claim that there is nothing to warn about.
    expect(deleteAccountNotice(null).warning).toBe(null);
    expect(deleteAccountNotice(null).listings).toBe(null);
  });

  it("drops the live warning only when eBay was really checked and had none", () => {
    expect(deleteAccountNotice({ ...COUNTED, live_listings: 0 }).warning)
      .toBe(null);
  });

  it("does not claim an eBay connection it could not verify", () => {
    expect(deleteAccountNotice({}).ebayConnected).toBe(false);
    expect(deleteAccountNotice(COUNTED).ebayConnected).toBe(true);
  });

  it("says nothing about a count of zero it did read", () => {
    // A real, read zero is not a warning and not a number worth naming.
    const n = deleteAccountNotice({ counted: true, listings: 0,
                                    live_listings: 0 });
    expect(n.listings).toBe(null);
    expect(n.warning).toBe(null);
  });
});

/**
 * What the "Delete your account?" dialog is entitled to say.
 *
 * Deleting removes Thryft Shop's copy of a listing. It does NOT end the
 * listing on eBay: anything already published stays live under the seller's
 * own eBay account and keeps selling — and keeps taking orders they can no
 * longer see from here. That warning is the reason the dialog exists, and
 * /api/account/summary carries `counted` so it can be given even when the
 * numbers cannot be read. Its docstring: "Silently showing '0 live listings'
 * would suppress exactly the warning this endpoint exists to" give.
 *
 * The rule, in one line: only a count that was actually READ may be named,
 * and anything else warns. Three inputs have to be told apart and the view
 * kept collapsing the last two:
 *
 *   null           nothing asked yet — say nothing, claim nothing
 *   {counted:false} the server tried and couldn't — warn without numbers
 *   {}             the request itself failed — the same thing, and it used to
 *                  fall through BOTH branches (`counted === false` is false
 *                  for undefined, and there was no live_listings to be truthy)
 *                  so the seller confirmed an irreversible delete having been
 *                  told only that it "erases your account and everything saved
 *                  to it".
 *
 * A pure function so those three can be tested without standing up the
 * Settings screen. Same reasoning as lib/listingsView and lib/settingsSections.
 */

/** @returns {{known: boolean, listings: number|null, ebayConnected: boolean,
 *             warning: null | {kind: "unknown"} | {kind: "live", count: number}}} */
export function deleteAccountNotice(summary) {
  if (!summary) {
    // Not asked yet. The dialog is still opening; it has nothing to report and
    // must not imply there is nothing to report.
    return { known: false, listings: null, ebayConnected: false, warning: null };
  }
  // `true` and only `true`. An absent flag is a summary that never arrived,
  // not a successful count.
  const known = summary.counted === true;
  const live = known ? Number(summary.live_listings) || 0 : 0;
  const listings = known ? Number(summary.listings) || 0 : 0;
  return {
    known,
    // A read zero is not worth naming; it also is not a warning.
    listings: known && listings > 0 ? listings : null,
    // NOT gated on `known`: the eBay flag comes from a different query than
    // the listing counts, so a summary that arrived with counted=false still
    // answered this one accurately. An absent field is already false, which
    // covers the case where nothing arrived at all.
    ebayConnected: !!summary.ebay_connected,
    warning: !known ? { kind: "unknown" }
      : live > 0 ? { kind: "live", count: live }
        : null,
  };
}

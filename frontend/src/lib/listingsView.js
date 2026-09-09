/**
 * Which of five things the listings area is looking at.
 *
 * The one that was missing is `unavailable`. A failed `/api/listings` left the
 * cache at its initial `items: []` and set `loaded: true`, so the store the
 * app could not read rendered as the store the seller does not have: "No
 * listings yet", with a button to create their first one, shown to someone
 * who may have four hundred. The toast that said otherwise was gone in five
 * seconds.
 *
 * This is the same distinction `metricsStatus` already makes for eBay's
 * traffic numbers — "we couldn't ask" rather than everything at zero — applied
 * to the store itself.
 */
export function listingsView({
  loading = false, loaded = false, error = "", dbConfigured = true,
  user = null, count = 0, truncated = false, total = null,
} = {}) {
  if (loading && !loaded) return { kind: "loading" };
  if (!dbConfigured) return { kind: "no-db" };
  if (!user) return { kind: "logged-out" };
  // Only when there is nothing to show. A refresh that fails while the
  // previous answer is still on screen should leave it there rather than
  // replacing a real store with an error card.
  if (error && !count) {
    return {
      kind: "unavailable",
      // Shown as it arrived. Whatever `api()` throws is already a complete
      // sentence written for the seller -- it writes one for a timeout, one
      // for a dropped connection, and since the P2-07 pass the server writes
      // one too. Wrapping that in a second sentence put the same words on
      // screen twice, around a status code: "We couldn’t load your listings
      // ((503) We couldn’t load your listings just now.). This doesn’t mean
      // you don’t have any". The reassurance now lives in db.list_listings’
      // message, where the layer that knows the store read failed can say it.
      message: error.trim()
        || "We couldn’t load your listings just now — this doesn’t mean you "
           + "don’t have any. Try again in a moment.",
    };
  }
  if (!count) return { kind: "empty" };
  // A page that isn't the whole store. Everything above this line is about
  // what the area may CLAIM, and "here are your listings" is a claim about all
  // of them: the counts, the tabs, the dashboard groups and the checkboxes a
  // bulk reprice runs over are all built on the page, none of them able to
  // tell it was cut. Same rule as the awaiting-shipment list and the sampled
  // status sweep -- say what could not be shown, and don't invent the rest.
  //
  // The total is named only when somebody counted one. The endpoint asks for
  // one row more than it returns -- free, on every load -- so `truncated`
  // always arrives; the COUNT(*) behind `total` runs only for the seller
  // actually past the cap, and can fail on its own. Absent, or smaller than
  // the page it claims to describe, it is left out entirely: "there are more"
  // is honest, and a number nobody counted is not.
  const named = Number.isFinite(total) && total > count;
  return {
    kind: "list",
    notice: truncated
      ? (named
        ? "This is the most recent " + fmt(count) + " of " + fmt(total)
          + " listings — more than we can show on one page."
        : "This is the most recent " + count + " of your listings — there are "
          + "more than we can show on one page.")
        + " Bulk actions here apply only to what's shown."
      : "",
  };
}

/** Thousands separators, so "3,000 of 4,127" reads as two numbers. */
function fmt(n) {
  return Number(n).toLocaleString("en-US");
}


/**
 * What one of the dashboard's store totals may say.
 *
 * The four tiles across the top -- Active on eBay, Drafts in progress, Sold,
 * Listed today -- are all counted off the same page of listings that
 * `listingsView` above is about. When that read fails the page is empty, so
 * every tile counts zero and then states it as a fact: "Active on eBay 0 /
 * everything currently live", "Sold $0.00 / nothing in the last 7 days".
 * Nothing there was measured.
 *
 * It matters more here than in most places a zero appears, because a seller
 * reads these to decide what to do next. Nothing live is a reason to go list
 * something; nothing sold in a week is a reason to cut prices. Both
 * conclusions, on a morning when the database was briefly slow -- and the
 * card directly below was saying "we couldn't load your listings, this
 * doesn't mean you don't have any" at the same moment, from the same failed
 * read.
 *
 * Same rule as `metricsStatus` for eBay's traffic numbers and `checked` on
 * the notification bell: a number nobody could measure is a dash, not a zero.
 */
export function storeTotal(kind, value, sub) {
  if (kind === "unavailable") {
    return { value: "—", sub: "we couldn’t check just now" };
  }
  return { value, sub };
}


/* A listing still in the seller's hands: an AI draft, or a dry-run one made
   without eBay connected. The cards that offer draft-only controls (the
   category quick-pick, the publish button) all ask this, so "draft" means the
   same thing on the Sell screen, the dashboard and the listings manager. */
export const isDraft = (item) => item?.status === "draft"
  || item?.status === "dry_run";


/* The statuses that have left the pipeline. A sale is finished business, and
   so is an ending: the listings manager files both under Inactive and
   subtracts them from the everything-tab, because a finished listing among
   the live ones is the one thing in that grid the seller cannot act on.

   `ended` joined this list with the automatic removal. An ended card sitting
   in All beside the live ones was the seller's actual report — and while an
   ending is no longer permanent here (a listing of theirs is kept for a
   month so they can relist it), the archive is where it belongs in the
   meantime, not the everything-tab.

   It lives here rather than inline in the tab table because the dashboard's
   "Recent listings" strip has to ask the same question, and the two used to
   disagree — an item that sold left the Sell screen and stayed on the
   dashboard, still offering "Edit live" on a listing that was already gone.
   One list, both readers. */
export const ARCHIVED_STATUSES = ["sold", "ended"];

export const isArchived = (item) => ARCHIVED_STATUSES.includes(item?.status);


/* The newest listings the seller can still do something about, most recently
   touched first — what the dashboard shows under "Recent listings".

   The archived ones are dropped BEFORE the slice, not after. Filtering the top
   four would hand back two cards to a seller whose last two events were sales,
   and nothing at all to one who just cleared out a batch, while older live
   listings sat there waiting to be shown. */
export function recentListings(items, limit = 4) {
  return (items || [])
    .filter((i) => !isArchived(i))
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""))
    .slice(0, limit);
}


/* A live listing a buyer has ACTED on: a bid on an auction, or a Best Offer
   waiting for an answer. Either one is money on the table, and both come
   from the same eBay read (services/metrics) with the same rule: the count
   is ABSENT, not zero, when the app could not ask — so a missing number is
   "we don't know", never "nobody". Only live listings qualify; a bid or an
   offer on anything else is settled history.

   This is the one question behind the green glow on the card and its place
   at the top of the grid, so it is asked in one place and answered once. */
export const isLive = (item) => item?.status === "published"
  || item?.status === "live";

export function buyerWaiting(item, metrics) {
  if (!isLive(item) || !metrics) return false;
  return Number(metrics.offers) > 0 || Number(metrics.bids) > 0;
}


/* The grid's order: the listings a buyer is waiting on first, then newest
   touched first — which is the order the grid always had, and is still the
   order within each half.

   The lift is the whole point of knowing. A bid or an offer used to be a
   chip on a card that sat wherever its last edit put it, which on a store of
   a few hundred is below the fold. The card the seller has to look at now
   is the one they should not have to scroll for. Stable, so two lifted cards
   keep their own recency order rather than whichever sorted first. */
export function orderListings(items, metricsById = {}) {
  const byRecency = (a, b) => (b.updated_at || "").localeCompare(a.updated_at || "");
  return [...(items || [])].sort((a, b) => {
    const lift = Number(buyerWaiting(b, metricsById?.[b.id]))
      - Number(buyerWaiting(a, metricsById?.[a.id]));
    return lift || byRecency(a, b);
  });
}


/* How long a listing of the seller's own is kept after it ends without
   selling. The server decides (listing_sync.ENDED_GRACE_DAYS) and publishes
   the number on /api/health; this is only what to say before that answer has
   arrived, so a dialog never promises a month the sweep does not measure. */
export const DEFAULT_ENDED_GRACE_DAYS = 30;

export function endedGraceDays(health) {
  const days = Number(health?.ended_grace_days);
  return Number.isFinite(days) && days > 0 ? days : DEFAULT_ENDED_GRACE_DAYS;
}


/* Does ending this listing KEEP it (for the grace period above) or remove it
   there and then?

   The server decides, and its answer comes back as `removed` — this is for
   the dialog shown BEFORE the call, which has to say which one it is about
   to do. It mirrors listing_sync.keeps_grace and must stay in step with it:

     * a record the store sync made (`ebay-<item>`) holds nothing the seller
       made here, and eBay stops serving an ended item's photos within weeks,
       so it goes at once — unless
     * the seller added photos to it here, which are then the only copy;
     * everything else is a listing this app created, and is kept. */
export function keptWhenEnded(item) {
  if (!String(item?.id || "").startsWith("ebay-")) return true;
  return (item?.listing?.images || []).length > 0;
}

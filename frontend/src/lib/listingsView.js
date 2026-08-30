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
  user = null, count = 0, truncated = false,
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
      message: `We couldn’t load your listings (${error}). This doesn’t mean `
        + `you don’t have any — try again in a moment.`,
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
  // No total, deliberately: the endpoint asks for one row more than it
  // returns rather than paying for a COUNT(*) on the busiest screen in the
  // app, so it knows there ARE more and not how many. Saying "some" honestly
  // beats naming a number nobody counted.
  return {
    kind: "list",
    notice: truncated
      ? "This is the most recent " + count + " of your listings — there are "
        + "more than we can show on one page. Bulk actions here apply only to "
        + "what's shown."
      : "",
  };
}

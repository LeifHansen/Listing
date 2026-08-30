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
  user = null, count = 0,
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
  return count ? { kind: "list" } : { kind: "empty" };
}

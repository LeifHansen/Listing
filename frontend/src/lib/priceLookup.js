/**
 * What the price area is allowed to say.
 *
 * `/api/price-suggestions` can come back three ways, and two of them used to
 * render identically:
 *
 *   - it looked and found comparable listings          -> show them;
 *   - it looked and the market has nothing like this    -> say so;
 *   - it never got to look (eBay 429'd the shared app quota, the token
 *     expired, the request timed out) -> say THAT.
 *
 * The third one was being reported as the second. In the editor that reads
 * "No comparable listings found — try a simpler title or set a category
 * first", which blames the seller's title for an outage; in Shop Mode it
 * reads "No price estimate yet" to someone standing in a shop deciding
 * whether to buy the thing in their hand. Neither is a claim we can make from
 * a failed request.
 *
 * Same distinction the listings area makes between "you have no listings" and
 * "we couldn't load them", and the same `checked` flag the delete-account
 * preview uses for its count.
 */
export function priceView({ checked = true, suggestion = null } = {}) {
  if (!checked) {
    return {
      kind: "unavailable",
      message: "We couldn’t check eBay’s prices just now — this doesn’t mean "
        + "there’s nothing comparable. Try again in a moment.",
    };
  }
  if (!suggestion) {
    return {
      kind: "none",
      message: "No comparable listings found — try a simpler title or set a "
        + "category first.",
    };
  }
  return { kind: "estimate", message: "" };
}

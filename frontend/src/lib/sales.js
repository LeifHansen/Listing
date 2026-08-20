/* What the store actually earned — the money side of a sold listing.
 *
 * Two facts drive everything here:
 *
 *  - `price` is the ASKING price and stays that way after a sale. When an
 *    offer is accepted (or an auction closes, or the price was cut before it
 *    sold), the item goes for LESS, and eBay reports that amount only on the
 *    transaction. The backend stores it as `sold_price`; when it's missing we
 *    fall back to `price` and the UI says the number is approximate rather
 *    than quietly overstating the take.
 *  - `sold_at` is the sale date. A record's `updated_at` cannot stand in for
 *    it: imported listings carry their eBay START time, so a listing that
 *    went live in March would count as a March sale.
 */

// The dashboard tile's windows. Rolling durations, not calendar buckets —
// the labels say "last N" so the number can't be read as "this calendar
// month" when it isn't.
export const SOLD_RANGES = [
  { id: "day", label: "24 hours", long: "last 24 hours", days: 1 },
  { id: "week", label: "7 days", long: "last 7 days", days: 7 },
  { id: "month", label: "30 days", long: "last 30 days", days: 30 },
  { id: "90d", label: "90 days", long: "last 90 days", days: 90 },
];

export const DEFAULT_SOLD_RANGE = "week";

export function soldRange(id) {
  return SOLD_RANGES.find((r) => r.id === id)
    || SOLD_RANGES.find((r) => r.id === DEFAULT_SOLD_RANGE);
}

// What one unit actually sold for, and whether that's the real number or the
// asking price standing in for it.
export function salePrice(listing) {
  const l = listing || {};
  const sold = Number(l.sold_price);
  if (l.sold_price != null && !Number.isNaN(sold)) return sold;
  const asked = Number(l.price);
  return l.price != null && !Number.isNaN(asked) ? asked : null;
}

// True when we know what the buyer paid (rather than only what was asked).
export function hasSalePrice(listing) {
  return (listing || {}).sold_price != null;
}

// How far below the asking price it went, as { amount, percent }, or null
// when it sold at (or above) the ask — or we don't know both numbers.
export function saleDiscount(listing) {
  const l = listing || {};
  if (!hasSalePrice(l)) return null;
  const asked = Number(l.price);
  const paid = Number(l.sold_price);
  if (!(asked > 0) || Number.isNaN(paid) || paid >= asked) return null;
  return { amount: asked - paid, percent: Math.round(((asked - paid) / asked) * 100) };
}

// Units sold. A listing that sold at all moved at least one.
export function soldUnits(listing) {
  const q = Number((listing || {}).sold_quantity);
  return Number.isFinite(q) && q > 0 ? q : 1;
}

// Everything one sold listing brought in.
export function saleProceeds(listing) {
  const each = salePrice(listing);
  return each == null ? null : each * soldUnits(listing);
}

export function saleTime(item) {
  const at = Date.parse((item?.listing || {}).sold_at || "");
  return Number.isNaN(at) ? null : at;
}

/* Totals for the sold listings inside one window.
 *
 * `undated` counts sold records with no sale date at all — records synced
 * before the app started recording one. They're deliberately NOT counted
 * (dating them to "whenever we noticed" would put old sales in this week);
 * the next store sync backfills them from eBay's own transaction dates, and
 * the tile says so rather than leaving the seller to wonder.
 */
export function salesSummary(items, rangeId, now = Date.now()) {
  const range = soldRange(rangeId);
  const since = now - range.days * 24 * 60 * 60 * 1000;
  let total = 0;
  let count = 0;
  let units = 0;
  let profit = 0;
  let withCost = 0;
  let approx = 0;
  let undated = 0;
  for (const item of items || []) {
    if (item.status !== "sold") continue;
    const at = saleTime(item);
    if (at == null) { undated += 1; continue; }
    if (at < since || at > now) continue;
    const l = item.listing || {};
    const proceeds = saleProceeds(l);
    if (proceeds == null) continue;
    count += 1;
    units += soldUnits(l);
    total += proceeds;
    if (!hasSalePrice(l)) approx += 1;
    if (l.purchase_price != null && !Number.isNaN(Number(l.purchase_price))) {
      withCost += 1;
      profit += proceeds - Number(l.purchase_price) * soldUnits(l);
    }
  }
  return {
    range, since, total, count, units, undated, approx,
    profit: withCost ? profit : null,
    withCost,
  };
}

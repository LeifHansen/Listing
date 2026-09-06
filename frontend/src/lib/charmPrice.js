/**
 * Where a price this app chooses lands: the nearest .99.
 *
 * Mirrors `charm_price` in backend/money.py, which shapes every price the
 * server picks — the AI's drafted price, the market number that overrules a
 * draft priced far under it, the headline comp suggestion, a bulk percentage
 * cut. This copy exists for the one place the choice happens in the browser:
 * the comps in the price card, where tapping a row turns a number measured off
 * the market into the number on the seller's own listing. Keep the two in
 * step; charmPrice.test.js checks this one against the same table the backend
 * test uses.
 *
 * Nearest, not always down — $22.50 becomes $22.99 — because the app's rule
 * everywhere else is never to shave a price "to be safe". A price the seller
 * TYPES is theirs and never comes through here.
 */

/** The floor, matching backend money.MIN_CHARM_PRICE and bulk MIN_PRICE. */
export const MIN_CHARM_PRICE = 0.99;

/** `amount` on the nearest .99, or null when it isn't a price at all. */
export function charmPrice(amount) {
  const value = Number(amount);
  if (!Number.isFinite(value) || value <= 0) return null;
  // Charm points sit one cent under a whole dollar, so shifting up by that
  // cent makes this "round to the nearest dollar" in whole cents — no float
  // rounding that can land on 24.990000000001.
  const cents = Math.round(value * 100);
  const nearest = Math.floor((cents + 51) / 100) * 100 - 1;
  return Math.max(nearest, Math.round(MIN_CHARM_PRICE * 100)) / 100;
}

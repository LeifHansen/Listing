/* Selling format — Buy It Now, auction, or both — in one place.
 *
 * eBay sells one item three ways, and this app stores which in
 * `listing.listing_format`. Which one is chosen decides WHICH FIELD holds the
 * asking price, and that is the whole reason this module exists:
 *
 *   FIXED_PRICE   price                        — Buy It Now, the default
 *   AUCTION       auction_start_price          — `price` is unused
 *   AUCTION_BIN   auction_start_price + price  — `price` is the Buy It Now
 *
 * Read `price` and nothing else and every auction in the store reads as
 * "no price yet": an auction imported from eBay has no `price` at all
 * (services/ebay_trading._item_to_listing sets it from BuyItNowPrice, which a
 * plain auction does not have). The card helpers below ask the format first.
 *
 * The labels live here too, so the three places a seller can PICK a format —
 * the editor's Pricing card, the bulk queue, the quick pick on a draft card —
 * cannot drift into three different names for the same thing.
 */

export const FIXED_PRICE = "FIXED_PRICE";
export const AUCTION = "AUCTION";
export const AUCTION_BIN = "AUCTION_BIN";

// "Both" rather than "Auction + BIN": BIN is eBay seller jargon, and this is
// the control a first-time seller meets. What it means is spelled out in
// FORMAT_HELP, which every picker hangs off the label.
export const LISTING_FORMATS = [
  [FIXED_PRICE, "Buy It Now"],
  [AUCTION, "Auction"],
  [AUCTION_BIN, "Both"],
];

// The same three, named for somewhere they stand ALONE — the chip on a card,
// which has no siblings to be "both" of. "Both" only reads as a format while
// the other two are on screen beside it; on a card it is a word with no
// referent, which is why this is a second list rather than a reuse of the
// one above.
export const FORMAT_CHIP_LABELS = {
  [FIXED_PRICE]: "Buy It Now",
  [AUCTION]: "Auction",
  [AUCTION_BIN]: "Auction + BIN",
};

export const FORMAT_HELP = {
  [FIXED_PRICE]: "One fixed price. It sells the moment somebody pays it.",
  [AUCTION]: "Buyers bid. It sells to the highest bid when the auction ends.",
  [AUCTION_BIN]:
    "An auction that also carries a Buy It Now price — buyers can bid, or "
    + "pay the Buy It Now to end it early.",
};

export const AUCTION_DURATIONS = [
  ["DAYS_1", "1 day"], ["DAYS_3", "3 days"], ["DAYS_5", "5 days"],
  ["DAYS_7", "7 days"], ["DAYS_10", "10 days (eBay charges extra)"],
];

// Short forms for a card, where "10 days (eBay charges extra)" doesn't fit.
const DURATION_DAYS = {
  DAYS_1: "1 day", DAYS_3: "3 days", DAYS_5: "5 days",
  DAYS_7: "7 days", DAYS_10: "10 days",
};

/** The stored value as one of the three formats. Anything else — "", null, a
 *  value from a listing saved before the field existed — is Buy It Now, which
 *  is what the model defaults to (backend/models.py Listing.listing_format). */
export function normalizeFormat(value) {
  const fmt = String(value || "").trim().toUpperCase();
  return fmt === AUCTION || fmt === AUCTION_BIN ? fmt : FIXED_PRICE;
}

/** The format of one listing (the `listing` object, not the record). */
export function listingFormat(listing) {
  return normalizeFormat((listing || {}).listing_format);
}

/** True for the two formats that take bids — both of which need a starting
 *  bid, and neither of which takes a quantity above 1. */
export function isAuctionFormat(value) {
  const fmt = normalizeFormat(value);
  return fmt === AUCTION || fmt === AUCTION_BIN;
}

/** The name to use where the format is reported on its own, not offered
 *  beside the other two. */
export function formatChipLabel(value) {
  return FORMAT_CHIP_LABELS[normalizeFormat(value)];
}

export function durationLabel(value) {
  return DURATION_DAYS[String(value || "").trim().toUpperCase()] || "7 days";
}

/** One line naming the format and the money it is asking, for a tooltip.
 *  Amounts are formatted by the caller, which owns the currency. */
export function formatSummary(listing, money) {
  const l = listing || {};
  const fmt = listingFormat(l);
  const start = money(l.auction_start_price);
  const price = money(l.price);
  if (fmt === AUCTION) {
    return start
      ? `Auction — bidding starts at ${start} and runs ${durationLabel(l.auction_duration)}.`
      : `Auction, running ${durationLabel(l.auction_duration)} — it still needs a starting bid.`;
  }
  if (fmt === AUCTION_BIN) {
    const parts = [`Auction with a Buy It Now, running ${durationLabel(l.auction_duration)}`];
    parts.push(start ? `bidding starts at ${start}` : "it still needs a starting bid");
    parts.push(price ? `Buy It Now ${price}` : "it still needs a Buy It Now price");
    return `${parts[0]} — ${parts[1]}, ${parts[2]}.`;
  }
  return price ? `Buy It Now at ${price}.` : "Buy It Now — it still needs a price.";
}

/* What one listing is ASKING, and what to call that number.
 *
 * `amount` is null when the field the format uses is empty, which is what
 * makes a card say "no price yet" — the honest answer for a draft nobody has
 * priced yet, and a wrong one for the auction it used to be shown on.
 *
 * A plain auction is quoted at its STARTING BID with the label to say so: an
 * auction has no asking price, and printing the opening bid bare next to a
 * grid of Buy It Now prices reads as one. An auction with a Buy It Now is
 * quoted at the Buy It Now — the number a buyer can pay right now — and the
 * opening bid rides along in the tooltip.
 */
export function askingPrice(listing) {
  const l = listing || {};
  const fmt = listingFormat(l);
  const num = (v) => {
    const n = Number(v);
    return v != null && v !== "" && Number.isFinite(n) ? n : null;
  };
  if (fmt === AUCTION) return { amount: num(l.auction_start_price), prefix: "Bid" };
  if (fmt === AUCTION_BIN) {
    const bin = num(l.price);
    return bin != null
      ? { amount: bin, prefix: null }
      : { amount: num(l.auction_start_price), prefix: "Bid" };
  }
  return { amount: num(l.price), prefix: null };
}

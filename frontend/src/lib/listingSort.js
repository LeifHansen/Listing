/* The order the Manage grid is in, when the seller picks one.
 *
 * The grid always had one order — the one `orderListings` gives: a listing a
 * buyer is waiting on first, then everything still in play, then the archive,
 * newest touched first within each. That is still the default ("Recommended")
 * because it is the right answer to "what should I look at", but it is the
 * wrong answer to every other question a seller brings to a list of a few
 * hundred things: "what have I had up the longest", "what are my most
 * expensive pieces", "where is that jacket, alphabetically".
 *
 * A sort is a VIEWING preference, not a filter. It hides nothing, the button
 * says which one is on, and so it is remembered on the device the way the
 * grid/list layout is (store.jsx `listingsSort`) — unlike the filters, which
 * are deliberately never restored silently, because a narrowing nobody can
 * see looks like missing listings.
 *
 * Two rules every sort below keeps:
 *
 * A listing that cannot answer the question goes LAST, whichever direction
 * the sort runs. An unpriced draft is neither the cheapest nor the dearest
 * thing in the store, and a listing eBay has not reported watchers for has
 * not got zero of them. Putting the unknowns first in one direction would
 * make "lowest price" open on a row of blanks.
 *
 * Ties keep the default's tie-break (most recently touched first), so two
 * listings at $20 sit in the same order under "price" that they would under
 * "Recommended", and the order is stable from one render to the next.
 */

import { byRecency, listedAt, orderListings } from "@/lib/listingsView";
import { filterPrice } from "@/lib/listingFilters";

export const DEFAULT_SORT = "recommended";

/* In the order the menu offers them: the default, then the dates (the
 * questions resellers ask most), then money, then the alphabet, then
 * buyer interest. */
export const SORTS = [
  { id: "recommended", label: "Recommended",
    hint: "Buyers waiting first, then newest changes" },
  { id: "listed-desc", label: "Newest listed" },
  { id: "listed-asc", label: "Oldest listed" },
  { id: "price-desc", label: "Price: high to low" },
  { id: "price-asc", label: "Price: low to high" },
  { id: "title-asc", label: "Title: A–Z" },
  { id: "title-desc", label: "Title: Z–A" },
  { id: "watchers-desc", label: "Most watchers" },
  { id: "views-desc", label: "Most views" },
  { id: "ending-asc", label: "Ending soonest", hint: "Auctions" },
  { id: "updated-desc", label: "Recently changed" },
];

const IDS = new Set(SORTS.map((s) => s.id));

export function normalizeSort(id) {
  const v = String(id ?? "");
  return IDS.has(v) ? v : DEFAULT_SORT;
}

export function sortLabel(id) {
  return SORTS.find((s) => s.id === normalizeSort(id)).label;
}

/* ---------- what each sort asks of a listing ---------- */

const num = (v) => {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const time = (iso) => {
  const t = Date.parse(iso || "");
  return Number.isNaN(t) ? null : t;
};

/* Watchers the way the card shows them: eBay's traffic read when there is
   one, otherwise the count the store sync copied onto an imported listing.
   Absent is unknown, not zero — services/metrics leaves it out when it could
   not ask. */
export function watcherCount(item, metrics) {
  if (metrics && metrics.watchers != null) return num(metrics.watchers);
  if (item?.listing?.source === "ebay") return num(item.listing.watch_count);
  return null;
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const titleOf = (item) => String(item?.listing?.title || item?.title || "").trim();

/* Each sort is a key and a direction. `key` returns null for "can't say",
   which the comparator below always sends to the end. */
const KEYS = {
  "listed-desc": { key: (i) => time(listedAt(i)), dir: -1 },
  "listed-asc": { key: (i) => time(listedAt(i)), dir: 1 },
  "price-desc": { key: (i) => filterPrice(i), dir: -1 },
  "price-asc": { key: (i) => filterPrice(i), dir: 1 },
  "title-asc": { key: (i) => titleOf(i) || null, dir: 1, text: true },
  "title-desc": { key: (i) => titleOf(i) || null, dir: -1, text: true },
  "watchers-desc": { key: (i, m) => watcherCount(i, m), dir: -1 },
  "views-desc": { key: (i, m) => num(m?.views), dir: -1 },
  // Only a live auction has an end the seller can act on; eBay's fixed-price
  // listings renew every 30 days and `ends_at` is only reported for auctions
  // (services/ebay_trading._auction_ends_at).
  "ending-asc": { key: (i, m) => time(m?.ends_at), dir: 1 },
  "updated-desc": { key: (i) => time(i?.updated_at), dir: -1 },
};

/* A sorted COPY. The store's `items` is what React renders from, so it is
   never sorted in place. */
export function sortListings(items, sortId, metricsById = {}) {
  const id = normalizeSort(sortId);
  if (id === DEFAULT_SORT) return orderListings(items, metricsById);
  const { key, dir, text } = KEYS[id];
  const keyed = (items || []).map((item) => ({
    item, k: key(item, metricsById?.[item.id]),
  }));
  keyed.sort((a, b) => {
    if (a.k == null || b.k == null) {
      if (a.k == null && b.k == null) return byRecency(a.item, b.item);
      return a.k == null ? 1 : -1;
    }
    const c = text ? collator.compare(a.k, b.k) : a.k - b.k;
    return (c * dir) || byRecency(a.item, b.item);
  });
  return keyed.map((x) => x.item);
}

/* ---------- saying why a card is where it is ---------- */

const shortDate = (iso) => new Date(iso).toLocaleDateString(undefined, {
  month: "short", day: "numeric",
  // A year only when it is not this one, so the line stays short for the
  // listings a seller is actually working on.
  ...(new Date(iso).getFullYear() === new Date().getFullYear() ? {} : { year: "numeric" }),
});

/* One short line for a card under a date sort — the fact the grid is ordered
   by, which a card does not otherwise show. "" for every other sort: price,
   title, watchers and views are already on the card. A string, not an
   element, because ListingCard is memo'd and a fresh node per render would
   fail its comparison on every card. */
export function sortMeta(item, sortId) {
  const id = normalizeSort(sortId);
  if (id === "listed-desc" || id === "listed-asc") {
    const at = listedAt(item);
    return at ? `Listed ${shortDate(at)}` : "";
  }
  if (id === "updated-desc") {
    return item?.updated_at && time(item.updated_at) != null
      ? `Changed ${shortDate(item.updated_at)}` : "";
  }
  return "";
}

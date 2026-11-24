/* Cutting the store down to the listings a seller is actually working on —
 * and keeping the cut, under a name, so tomorrow costs one tap.
 *
 * The listings grid had two ways to narrow a store: the lifecycle tabs
 * (Active / Finds / Inactive / All) and the search box, which matches a title,
 * a brand or a description. Both are about one listing at a time. Neither
 * answers the questions a seller with a few hundred items actually has —
 * "which auctions have I not priced", "everything Nike under $20", "the
 * drafts still missing photos" — and a seller who worked one of those out by
 * scrolling had to work it out again the next morning.
 *
 * So: a set of filters that stack, and a saved view that is a name for a tab
 * plus a set of filters. This module is all of the deciding and none of the
 * drawing, for the same reason lib/listingsView.js is: the grid, its counts
 * and its empty state all have to agree about which listings are showing, and
 * three components each doing their own filtering is three chances to
 * disagree.
 *
 * Two rules run through everything below.
 *
 * A filter NARROWS, it never invents. Every predicate is asked of a fact the
 * record carries, and a listing the fact is missing from fails the filter
 * rather than passing it: an unpriced draft is not "under $20", and a listing
 * with no `created_at` is not "listed this week". The seller is told this
 * where it could surprise them (the price help text), because the alternative
 * — quietly including what could not be measured — is the same mistake as a
 * tab badge counting an outage as zero.
 *
 * And a saved view is a QUESTION, not a snapshot. It stores the tab and the
 * filters, never the listings they matched, so a view called "Needs photos"
 * empties as the photos get taken. A view that stored ids would go stale the
 * first time it was used, which is the one thing a saved list must not do.
 */

import { buyerWaiting, isArchived, listedAt } from "@/lib/listingsView";
import { CONDITIONS, conditionLabel } from "@/lib/conditions";
import {
  AUCTION, AUCTION_BIN, FIXED_PRICE, FORMAT_CHIP_LABELS, askingPrice,
  listingFormat,
} from "@/lib/listingFormat";
import { salePrice } from "@/lib/sales";

/* ---------- the vocabulary ---------- */

// The three selling formats, in the order the pickers offer them.
export const FORMAT_CHOICES = [FIXED_PRICE, AUCTION, AUCTION_BIN];

// Photos: any, has at least one, has none. A draft with no photos cannot be
// published and cannot be judged, so "No photos" is a worklist.
export const PHOTO_CHOICES = [
  ["any", "Any"],
  ["with", "Has photos"],
  ["without", "No photos"],
];

// How recently the listing went UP (lib/listingsView.listedAt — eBay's start
// time where there is one, not the night the store sync first imported it).
// Rolling windows, and the labels say
// "last N" so none of them can be read as a calendar month — the same wording
// rule the sold-tile ranges follow (lib/sales.SOLD_RANGES).
export const AGE_CHOICES = [
  ["", "Any time"],
  ["1", "Last 24 hours"],
  ["7", "Last 7 days"],
  ["30", "Last 30 days"],
  ["90", "Last 90 days"],
];

// Buyer interest: has anybody looked hard at it? "Has watchers" is the
// shortlist for "Send offers"; "Offers or bids" is money on the table — the
// same question the green glow on a card answers (listingsView.buyerWaiting).
export const INTEREST_CHOICES = [
  ["", "Any"],
  ["watchers", "Has watchers"],
  ["offers", "Offers or bids"],
];

// The store-category pick for a listing on no shelf at all. eBay files those
// at the top of the Store ("Other items"); a seller tidying their shelves
// wants exactly that list. A word rather than "" because "" is what an empty
// multi-select already means, and the two must not collide.
export const NO_STORE_CATEGORY = "none";

// How many category ids one filter may hold. The same bound the server
// applies to any list in a saved view (main._VIEW_LIST_MAX).
const ID_LIST_MAX = 40;

// Text fields are capped before they are stored: these ride in a saved view,
// which rides in the user's prefs JSON, and an unbounded string there is a
// row the seller can grow without limit.
const TEXT_MAX = 60;

/* Nothing selected. Frozen because it is handed out as the starting value and
   as the value "Clear all" resets to — a caller that mutated it in place
   would change what "no filters" means for the rest of the session. */
export const EMPTY_FILTERS = Object.freeze({
  format: [],
  condition: [],
  brand: "",
  category: "",
  priceMin: "",
  priceMax: "",
  photos: "any",
  within: "",
  needsWork: false,
  // Category picks, by id — not by name, because a shelf can be renamed in
  // the Store and a picked id should keep meaning the same shelf.
  storeCategory: [],
  ebayCategory: [],
  interest: "",
});

/* ---------- reading a filter set ---------- */

const text = (v) => String(v ?? "").trim().slice(0, TEXT_MAX);

/* A price box's contents, as the box should hold them.
 *
 * Kept as a STRING in the filter object rather than as a number, because the
 * boxes are controlled inputs and a half-typed value has to survive a render
 * as itself. Rounding each keystroke through `Number` ate the decimal point:
 * "1." came back as "1", the box re-rendered as "1", and the next key made
 * "15" — so 1.50 could not be typed at all.
 *
 * What it keeps is digits and at most one point (two places, nine digits):
 * everything a price can be while it is still being typed, and nothing a
 * price can never be. */
const priceText = (v) => {
  const s = String(v ?? "").trim().replace(/[^\d.]/g, "");
  return (s.match(/^\d{0,9}(\.\d{0,2})?/) || [""])[0];
};

/* The same box as a number the predicate can compare, or null for one that
 * is empty or still only a decimal point. */
const priceValue = (v) => {
  const s = priceText(v);
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) && n >= 0 ? n : null;
};

const pick = (raw, allowed) => {
  const want = Array.isArray(raw) ? raw.map((v) => String(v)) : [];
  // Ordered by the canonical list rather than by how they were clicked, so
  // two sets with the same members are the same set — which is what lets
  // `sameFilters` below tell a saved view from the filters on screen.
  return allowed.filter((v) => want.includes(v));
};

const choiceIds = (choices) => choices.map(([id]) => id);

/* A list of ids as a filter should hold it: strings, each once, bounded, and
   SORTED — the open set has no canonical order the way formats do, and two
   sets with the same members have to compare equal for a saved view to light
   up as the one showing (sameFilters). */
const idList = (raw) => [...new Set(
  (Array.isArray(raw) ? raw : [])
    .map((v) => String(v ?? "").trim().slice(0, 40))
    .filter(Boolean),
)].sort().slice(0, ID_LIST_MAX);

/* A filter object with only the keys this module knows, each a value it can
   act on. Everything that arrives from outside goes through here: the store's
   setter, a saved view read back from the server, and the tests. */
export function normalizeFilters(raw) {
  const f = raw && typeof raw === "object" ? raw : {};
  const photos = String(f.photos ?? "any");
  const within = String(f.within ?? "");
  const interest = String(f.interest ?? "");
  return {
    format: pick(f.format, FORMAT_CHOICES),
    condition: pick(f.condition, CONDITIONS),
    brand: text(f.brand),
    category: text(f.category),
    priceMin: priceText(f.priceMin),
    priceMax: priceText(f.priceMax),
    photos: choiceIds(PHOTO_CHOICES).includes(photos) ? photos : "any",
    within: choiceIds(AGE_CHOICES).includes(within) ? within : "",
    needsWork: f.needsWork === true,
    storeCategory: idList(f.storeCategory),
    ebayCategory: idList(f.ebayCategory),
    interest: choiceIds(INTEREST_CHOICES).includes(interest) ? interest : "",
  };
}

/* Which dimensions are actually narrowing anything. The price boxes count as
   ONE — they are one question with two ends, and a "2 filters" badge for a
   single range reads as a miscount. */
function activeFilterKeys(filters) {
  const f = normalizeFilters(filters);
  const on = [];
  if (f.format.length) on.push("format");
  if (f.condition.length) on.push("condition");
  if (f.brand) on.push("brand");
  if (f.category) on.push("category");
  if (f.priceMin || f.priceMax) on.push("price");
  if (f.photos !== "any") on.push("photos");
  if (f.within) on.push("within");
  if (f.needsWork) on.push("needsWork");
  if (f.storeCategory.length) on.push("storeCategory");
  if (f.ebayCategory.length) on.push("ebayCategory");
  if (f.interest) on.push("interest");
  return on;
}

export function activeFilterCount(filters) {
  return activeFilterKeys(filters).length;
}

export function isEmptyFilters(filters) {
  return activeFilterKeys(filters).length === 0;
}

/* Reset one dimension, leaving the rest alone — what the × on a chip does.
   "price" clears both ends, matching how it is counted above. */
export function clearFilter(filters, key) {
  const f = normalizeFilters(filters);
  if (key === "price") return { ...f, priceMin: "", priceMax: "" };
  if (key in EMPTY_FILTERS) return { ...f, [key]: EMPTY_FILTERS[key] };
  return f;
}

/* ---------- what a listing is, for filtering ---------- */

/* The photos a card can actually show. Two fields because there are two kinds
   of record: listings made here store filenames in `images`, and listings
   imported from eBay carry eBay's own absolute URLs in `image_urls`. Asking
   only the first reports every imported listing in the store as having no
   photos. */
export function photoCount(listing) {
  const l = listing || {};
  return (Array.isArray(l.images) ? l.images.length : 0)
    + (Array.isArray(l.image_urls) ? l.image_urls.length : 0);
}

/* The number a price filter is about.
 *
 * A sold listing is asked what it WENT FOR, everything else what it is
 * ASKING — because "everything I sold over $50" and "everything listed under
 * $20" are both questions about money that changed (or would change) hands,
 * and on a sold record the asking price is the number that is no longer true.
 * `salePrice` already falls back to the ask when eBay has not reported the
 * amount, and `askingPrice` already knows that an auction's number lives in
 * `auction_start_price` — read `price` alone and every auction in the store
 * filters as unpriced.
 *
 * null means nobody has set one, which is a real answer and not a zero. */
export function filterPrice(item) {
  const listing = item?.listing || {};
  if (item?.status === "sold") return salePrice(listing);
  return askingPrice(listing).amount;
}

/* Is this listing missing something that stops it being published?
 *
 * The three blanks that do: no price in the field its format uses, no
 * category (eBay refuses the offer, and the category decides which conditions
 * it will even accept), no photos. It is the "what can I actually finish
 * today" filter, so it is asked of listings still in play — a sold or ended
 * record is finished business and is never short of anything. */
export function needsWork(item) {
  if (isArchived(item)) return false;
  const l = item?.listing || {};
  if (askingPrice(l).amount == null) return true;
  if (!String(l.category_id || l.category_suggestion || "").trim()) return true;
  return photoCount(l) === 0;
}

const has = (value, needle) =>
  String(value ?? "").toLowerCase().includes(needle);

/* ---------- the predicate ---------- */

/* Does one listing survive the filters?
 *
 * `now` is injected so the age windows are testable and so a grid rendered
 * in one pass measures every listing against the same instant.
 *
 * `ctx.metrics` is eBay's traffic read for THIS listing (store.metricsById),
 * which the buyer-interest filter needs and the record does not carry. */
export function matchesFilters(item, filters, now = Date.now(), ctx = {}) {
  const f = normalizeFilters(filters);
  const listing = item?.listing || {};

  if (f.format.length && !f.format.includes(listingFormat(listing))) return false;

  if (f.condition.length
      && !f.condition.includes(String(listing.condition || ""))) return false;

  if (f.brand && !has(listing.brand, f.brand.toLowerCase())) return false;

  if (f.category) {
    const needle = f.category.toLowerCase();
    // Both category systems, because a seller means either: eBay's own tree
    // ("Clothing > Men's > Shirts") and the shelf in their own Store
    // ("Vintage Tees"), which is their invention and the name they think in.
    const hit = has(listing.category_suggestion, needle)
      || has(listing.store_category_name, needle);
    if (!hit) return false;
  }

  const min = priceValue(f.priceMin);
  const max = priceValue(f.priceMax);
  if (min != null || max != null) {
    const price = filterPrice(item);
    // A listing nobody has priced is not in any range. Excluded rather than
    // kept: "under $20" that answers with the unpriced drafts is a list the
    // seller has to re-filter by eye, which is the work this exists to save.
    if (price == null) return false;
    if (min != null && price < min) return false;
    if (max != null && price > max) return false;
  }

  if (f.photos === "with" && photoCount(listing) === 0) return false;
  if (f.photos === "without" && photoCount(listing) > 0) return false;

  if (f.within) {
    const listed = Date.parse(listedAt(item));
    // Same rule as the price: a record that cannot say when it went up is
    // not evidence that it went up this week.
    if (Number.isNaN(listed)) return false;
    if (now - listed > Number(f.within) * 86400000) return false;
  }

  if (f.needsWork && !needsWork(item)) return false;

  if (f.storeCategory.length) {
    const shelf = String(listing.store_category_id || "").trim();
    // eBay reads "0" as the top level too (ebay_trading reads it as "").
    const key = shelf && shelf !== "0" ? shelf : NO_STORE_CATEGORY;
    if (!f.storeCategory.includes(key)) return false;
  }

  if (f.ebayCategory.length
      && !f.ebayCategory.includes(String(listing.category_id || "").trim())) return false;

  if (f.interest) {
    const m = ctx.metrics;
    if (f.interest === "offers" && !buyerWaiting(item, m)) return false;
    if (f.interest === "watchers") {
      // eBay's read first, the sync's copy on an imported listing second;
      // neither is "nobody", it is "we don't know" — and a filter narrows.
      const w = m && m.watchers != null ? Number(m.watchers)
        : listing.source === "ebay" && listing.watch_count != null
          ? Number(listing.watch_count) : null;
      if (!(w > 0)) return false;
    }
  }

  return true;
}

export function filterListings(items, filters, now = Date.now(), ctx = {}) {
  if (isEmptyFilters(filters)) return items || [];
  const metrics = ctx.metricsById || {};
  return (items || []).filter((i) => matchesFilters(i, filters, now, {
    ...ctx, metrics: metrics[i.id],
  }));
}

/* ---------- saying what is on ---------- */

const listOf = (values, label) => values.map(label).join(", ");

/* "Vintage Tees, Beanies +2": the first two picks by name and a count for
   the rest, so a chip stays one line however many shelves are ticked. */
const namedList = (ids, nameOf) => {
  const names = ids.map((id) => nameOf(id));
  return names.length > 2
    ? `${names.slice(0, 2).join(", ")} +${names.length - 2}`
    : names.join(", ");
};

/* One chip per active dimension, in a fixed order, each carrying the key its
   × clears. A chip per dimension rather than per VALUE: picking three
   conditions is one decision, and three chips for it reads as three filters
   the seller has to dismiss one at a time.

   `names` turns category ids back into what the seller calls them; without
   it (or for an id the store no longer has) the chip says so rather than
   showing a number. */
export function filterChips(filters, names = {}) {
  const f = normalizeFilters(filters);
  const chips = [];
  if (f.format.length) {
    chips.push({ key: "format",
      label: listOf(f.format, (v) => FORMAT_CHIP_LABELS[v]) });
  }
  if (f.condition.length) {
    chips.push({ key: "condition", label: listOf(f.condition, conditionLabel) });
  }
  if (f.brand) chips.push({ key: "brand", label: `Brand: ${f.brand}` });
  if (f.category) chips.push({ key: "category", label: `Category: ${f.category}` });
  if (f.priceMin || f.priceMax) {
    const min = f.priceMin, max = f.priceMax;
    chips.push({
      key: "price",
      label: min && max ? `${min}–${max}`
        : min ? `Over ${min}`
          : `Under ${max}`,
    });
  }
  if (f.photos !== "any") {
    chips.push({ key: "photos",
      label: PHOTO_CHOICES.find(([id]) => id === f.photos)[1] });
  }
  if (f.within) {
    chips.push({ key: "within",
      label: AGE_CHOICES.find(([id]) => id === f.within)[1] });
  }
  if (f.needsWork) chips.push({ key: "needsWork", label: "Needs work" });
  if (f.storeCategory.length) {
    const nameOf = (id) => (id === NO_STORE_CATEGORY ? "No store category"
      : names.storeCategory?.(id) || "Removed category");
    chips.push({ key: "storeCategory",
      label: `Store: ${namedList(f.storeCategory, nameOf)}` });
  }
  if (f.ebayCategory.length) {
    const nameOf = (id) => names.ebayCategory?.(id) || `#${id}`;
    chips.push({ key: "ebayCategory",
      label: `eBay: ${namedList(f.ebayCategory, nameOf)}` });
  }
  if (f.interest) {
    chips.push({ key: "interest",
      label: INTEREST_CHOICES.find(([id]) => id === f.interest)[1] });
  }
  return chips;
}

/* ---------- saved views ---------- */

// How many a seller may keep. They ride in the same per-user JSON the
// new-listing defaults live in (backend db.saved_listing_views), so the cap
// is what keeps one row from growing without limit — and twenty named lists
// is already more than a tab strip can usefully hold.
export const MAX_SAVED_VIEWS = 20;
export const VIEW_NAME_MAX = 40;

// Where a view lands you. Kept as a plain string rather than validated
// against ListingsView.TABS: that table is a component's, this is the store's
// vocabulary, and an unknown tab is already handled — the view falls back the
// same way a remembered tab from an older release does (STALE_TABS).
const DEFAULT_TAB = "active";

const viewId = () =>
  `v${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;

/* One stored view, or null if there is nothing usable in it. Every view that
   comes back from the server goes through this: it is the seller's own data,
   but it has been round-tripped through a JSON column and a release or two,
   and a view with no name is a pill nobody can read or delete. */
export function normalizeSavedView(raw) {
  const v = raw && typeof raw === "object" ? raw : {};
  const name = String(v.name ?? "").trim().slice(0, VIEW_NAME_MAX);
  if (!name) return null;
  return {
    id: String(v.id || "").slice(0, 64) || viewId(),
    name,
    tab: String(v.tab || DEFAULT_TAB).slice(0, 24) || DEFAULT_TAB,
    filters: normalizeFilters(v.filters),
    // The order the view was saved in, or "" for a view saved before sorts
    // existed — which applies its filters and leaves the sort alone.
    sort: String(v.sort || "").slice(0, 24),
    created_at: String(v.created_at || ""),
  };
}

export function normalizeSavedViews(raw) {
  const seen = new Set();
  return (Array.isArray(raw) ? raw : [])
    .map(normalizeSavedView)
    .filter(Boolean)
    // Two views with one id is a delete that removes the wrong one.
    .filter((v) => (seen.has(v.id) ? false : (seen.add(v.id), true)))
    .slice(0, MAX_SAVED_VIEWS);
}

/* Do two filter sets ask the same question? Compared field by field off the
   normalized form — which is why `pick` sorts the multi-selects into the
   canonical order: picking Auction then Buy It Now must equal picking them
   the other way round, or a view would never light up as the one showing. */
export function sameFilters(a, b) {
  const x = normalizeFilters(a), y = normalizeFilters(b);
  return Object.keys(EMPTY_FILTERS).every((k) => (
    Array.isArray(x[k])
      ? x[k].length === y[k].length && x[k].every((v, i) => v === y[k][i])
      : x[k] === y[k]
  ));
}

/* The saved view the screen is currently showing, or "". What lets the pill
   for it read as pressed instead of as something still to be applied.

   A view with a sort is showing only in that sort; one saved without a sort
   never asked for one, so any order matches it. */
export function matchingViewId(views, tab, filters, sort = "") {
  const hit = normalizeSavedViews(views).find(
    (v) => v.tab === tab && sameFilters(v.filters, filters)
      && (!v.sort || !sort || v.sort === sort));
  return hit ? hit.id : "";
}

/* Add a view, or say why not.
 *
 * A result object rather than a throw: both refusals are things the seller
 * did (an empty name, a full list), and both want the same sentence in the
 * same toast as the failures that come back from the server.
 *
 * Saving under a name that already exists REPLACES it, in place, keeping its
 * id and its position in the strip. That is the only update path there is —
 * a seller who tunes a view and saves it again means "this is what that list
 * is now", and the alternative is two pills with one name, which is worse
 * than either reading of the press. */
export function addSavedView(views, { name, tab, filters, sort = "", now = Date.now() } = {}) {
  const current = normalizeSavedViews(views);
  const clean = String(name ?? "").trim().slice(0, VIEW_NAME_MAX);
  if (!clean) return { ok: false, error: "Give this view a name first." };

  const view = {
    id: viewId(),
    name: clean,
    tab: String(tab || DEFAULT_TAB),
    filters: normalizeFilters(filters),
    sort: String(sort || "").slice(0, 24),
    created_at: new Date(now).toISOString(),
  };

  const at = current.findIndex(
    (v) => v.name.toLowerCase() === clean.toLowerCase());
  if (at >= 0) {
    const next = [...current];
    next[at] = { ...view, id: current[at].id };
    return { ok: true, views: next, view: next[at], replaced: true };
  }
  if (current.length >= MAX_SAVED_VIEWS) {
    return {
      ok: false,
      error: `You've saved ${MAX_SAVED_VIEWS} views — delete one to make room.`,
    };
  }
  return { ok: true, views: [...current, view], view, replaced: false };
}

export function removeSavedView(views, id) {
  return normalizeSavedViews(views).filter((v) => v.id !== id);
}

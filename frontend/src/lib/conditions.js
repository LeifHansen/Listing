/* Item condition — which grades exist, and which of them this category takes.

   eBay does not offer one ladder. "Very Good", "Good" and "Acceptable"
   (4000/5000/6000) exist only in media categories; the pre-owned grades
   (2990/3000/3010) only across Pre-loved Apparel; most of the rest of the site
   offers a bare "Used". A grade the category doesn't offer isn't a warning at
   publish time — eBay answers 25021 and there is no listing.

   So the category answers first and the condition follows. The server does
   this on every draft it creates and again before it publishes
   (backend/services/taxonomy.py, which these tables mirror — it is the
   authority; keep the two in step). This copy is what lets the editor and the
   bulk queue show the right choices while the seller is still typing. */

import { postJson } from "@/lib/api";

// Every grade the app can hold, best first. The dropdown's fallback list for
// a listing with no category yet — once there is one, eBay's own answer for
// that category replaces it.
export const CONDITIONS = [
  "NEW", "NEW_OTHER", "NEW_WITH_DEFECTS", "CERTIFIED_REFURBISHED",
  "SELLER_REFURBISHED", "LIKE_NEW", "PRE_OWNED_EXCELLENT", "USED_EXCELLENT",
  "USED_VERY_GOOD", "USED_GOOD", "PRE_OWNED_FAIR", "USED_ACCEPTABLE",
  "FOR_PARTS_OR_NOT_WORKING",
];

// How much wear each grade promises a buyer, on ONE scale so grades from
// different category families can be compared — the numbers are what let a
// refused condition be replaced by the CLOSEST one the category allows
// instead of the first in eBay's list, which is "New".
const QUALITY = {
  NEW: 100,
  NEW_OTHER: 90,
  NEW_WITH_DEFECTS: 80,
  CERTIFIED_REFURBISHED: 75,
  SELLER_REFURBISHED: 70,
  LIKE_NEW: 65,
  PRE_OWNED_EXCELLENT: 60,
  USED_EXCELLENT: 55,
  USED_VERY_GOOD: 48,
  USED_GOOD: 40,
  PRE_OWNED_FAIR: 20,
  USED_ACCEPTABLE: 20,
  FOR_PARTS_OR_NOT_WORKING: 0,
};

// Which side of the new/used line each grade sits on. A substitution never
// crosses it: a worn t-shirt relabelled "New" is a return and a defect on the
// seller's account, which is worse than the publish error it replaced.
const FAMILY = {
  NEW: "new", NEW_OTHER: "new", NEW_WITH_DEFECTS: "new",
  CERTIFIED_REFURBISHED: "refurbished", SELLER_REFURBISHED: "refurbished",
  LIKE_NEW: "used", PRE_OWNED_EXCELLENT: "used", USED_EXCELLENT: "used",
  USED_VERY_GOOD: "used", USED_GOOD: "used", PRE_OWNED_FAIR: "used",
  USED_ACCEPTABLE: "used", FOR_PARTS_OR_NOT_WORKING: "used",
};

export function conditionLabel(c) {
  return String(c || "").replaceAll("_", " ").toLowerCase()
    .replace(/\b\w/g, (m) => m.toUpperCase());
}

/* The condition to use when `current` isn't one this category offers.

   Returns `current` when it is already allowed (and when there is no list to
   check it against — an empty list means "we couldn't ask eBay", never "eBay
   allows anything"), the closest allowed grade in the same family when it
   isn't, and null when the category offers nothing in that family: a new-only
   category has no honest home for a used item, and the seller is told rather
   than having one picked for them. Ties go to the lower grade — understating
   wear costs a few dollars, overstating it costs the sale. */
export function nearestCondition(current, allowed) {
  const cur = String(current || "").trim().toUpperCase();
  const list = (allowed || []).map((c) => String(c || "").trim().toUpperCase())
    .filter(Boolean);
  if (!cur || !list.length || list.includes(cur)) return cur || null;
  const family = FAMILY[cur];
  const want = QUALITY[cur];
  if (!family || want === undefined) return null;
  const pool = list.filter((c) => FAMILY[c] === family && QUALITY[c] !== undefined);
  if (!pool.length) return null;
  return pool.reduce((best, c) => {
    const d = Math.abs(QUALITY[c] - want);
    const bd = Math.abs(QUALITY[best] - want);
    // Closer wins; on a tie the lower grade does.
    return d < bd || (d === bd && QUALITY[c] < QUALITY[best]) ? c : best;
  });
}

/* eBay's condition list for one category, fetched once per category per page
   load. The bulk queue renders forty cards that between them hold a handful of
   categories, and each card asking for its own copy is forty requests for four
   answers. The server caches these for a day; this stops the browser asking
   again in the same session. */
const _cache = new Map();

export function conditionsFor(categoryId) {
  const cid = String(categoryId || "").trim();
  if (!/^\d+$/.test(cid)) return Promise.resolve(null);
  if (!_cache.has(cid)) {
    _cache.set(cid, postJson("/api/item-conditions", { category_id: cid })
      .then((r) => (r.checked === false ? null : (r.conditions || [])))
      // A lookup we couldn't make is null — the generic list, and no blocker.
      .catch(() => null));
  }
  return _cache.get(cid);
}

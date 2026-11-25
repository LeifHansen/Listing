/* The choices the category quick filters offer, worked out from the store.
 *
 * Two category systems sit on every eBay listing and a seller means either:
 *
 *   * eBay's own tree — the leaf the listing is filed under ("Men's T-Shirts").
 *     There is no endpoint for "the categories this seller uses", and there
 *     does not need to be: every loaded listing carries its `category_id` and
 *     a readable name for it (`category_suggestion`), so the list is counted
 *     off the listings themselves. A category with nothing in it is not a
 *     choice worth offering.
 *   * the seller's own Store shelves — their invention, the names they think
 *     in. Those come from eBay (GET /api/ebay/store-categories) in the order
 *     the seller arranged them, so a shelf with nothing on it still shows,
 *     with a zero: "which shelves are empty" is a real question.
 *
 * The counts are taken BEFORE the filters (after the tab, the marketplace and
 * the search box), so ticking one category does not make every other choice
 * read zero and vanish — the list is a menu, not a result.
 */

import { NO_STORE_CATEGORY } from "@/lib/listingFilters";

/* The last step of a category path, in either of the two shapes the records
   carry: "Clothing > Men > Shirts" (what this app writes on a draft) or
   "Clothing, Shoes & Accessories:Men:Men's Clothing:Shirts" (eBay's
   PrimaryCategory/CategoryName on an imported listing). */
export function categoryLeaf(path) {
  const s = String(path || "").trim();
  if (!s) return "";
  const parts = s.includes(" > ") ? s.split(" > ") : s.split(":");
  return parts.map((p) => p.trim()).filter(Boolean).pop() || s;
}

/* Every eBay category the given listings are filed under, most used first. */
export function ebayCategoryOptions(items) {
  const byId = new Map();
  for (const item of items || []) {
    const l = item?.listing || {};
    const id = String(l.category_id || "").trim();
    if (!id) continue;
    const hit = byId.get(id);
    if (hit) {
      hit.count += 1;
      // The longest path wins: one listing may carry only the leaf and another
      // the whole trail, and the tooltip should have the trail.
      if (String(l.category_suggestion || "").length > hit.path.length) {
        hit.path = String(l.category_suggestion);
        hit.name = categoryLeaf(hit.path) || hit.name;
      }
    } else {
      const path = String(l.category_suggestion || "");
      byId.set(id, { id, name: categoryLeaf(path) || `Category ${id}`, path, count: 1 });
    }
  }
  return [...byId.values()].sort(
    (a, b) => (b.count - a.count) || a.name.localeCompare(b.name));
}

const shelfOf = (item) => {
  const id = String(item?.listing?.store_category_id || "").trim();
  return id && id !== "0" ? id : NO_STORE_CATEGORY;
};

/* The store's shelves in the store's own order, each with how many of the
   given listings sit on it; then a shelf a listing still points at that the
   store no longer has (renamed away or deleted in Seller Hub), under the name
   the listing remembers; then "No store category". */
export function storeCategoryOptions(categories, items) {
  const counts = new Map();
  const remembered = new Map();
  for (const item of items || []) {
    const id = shelfOf(item);
    counts.set(id, (counts.get(id) || 0) + 1);
    if (id !== NO_STORE_CATEGORY && !remembered.has(id)) {
      remembered.set(id, String(item?.listing?.store_category_name || ""));
    }
  }
  const known = new Set();
  const out = (categories || []).map((c) => {
    const id = String(c.id);
    known.add(id);
    return {
      id, name: c.name || c.path || id, path: c.path || c.name || "",
      level: c.level || 1, count: counts.get(id) || 0,
    };
  });
  for (const [id, name] of remembered) {
    if (known.has(id)) continue;
    out.push({ id, name: name || `Shelf ${id}`, path: "", level: 1,
      count: counts.get(id) || 0, orphan: true });
  }
  out.push({ id: NO_STORE_CATEGORY, name: "No store category", path: "",
    level: 1, count: counts.get(NO_STORE_CATEGORY) || 0 });
  return out;
}

/* id -> name, for the filter chips. */
export function optionNamer(options) {
  const m = new Map((options || []).map((o) => [o.id, o.name]));
  return (id) => m.get(id) || "";
}

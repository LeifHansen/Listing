/* The crosspost's own reading of a listing: can it go to Etsy, and if not
   yet, why. Pure, so the wizard, the bulk bar and the tests all ask the
   same function. */
import { etsyBlockers } from "@/views/listing/blockers";
import { isLive, onMarket } from "./listingsView";

// Why a selected listing is left out of a crosspost — "" when it can go.
// Each reason is a whole sentence the row shows; none of them is a field
// to fix from here, which is what separates a skip from a blocker.
export function crosspostSkipReason(item) {
  if (onMarket(item, "etsy")) return "Already on Etsy.";
  if (!isLive(item)) return "Not live on eBay — publish it first, or list it on Etsy from the editor.";
  const l = item?.listing || {};
  const fmt = String(l.listing_format || "FIXED_PRICE").toUpperCase();
  if (fmt !== "FIXED_PRICE") return "An auction — Etsy only does Buy It Now.";
  if (l.has_variations) return "Has size or colour variations, which Etsy gets as one price and one stock count.";
  return "";
}

/* One row per selected listing: the item, why it is skipped (if it is),
   and what Etsy still needs from it (judged against the shop's settings
   when they are loaded; against nothing when they are not — the profile
   rules are then left to the server, as everywhere else). */
export function candidates(items, { etsySettings = null, mode = "live" } = {}) {
  return (items || []).map((item) => {
    const skip = crosspostSkipReason(item);
    const blockers = skip ? [] : etsyBlockers(item.listing || {}, { settings: etsySettings, mode });
    return { item, skip, blockers, ready: !skip && blockers.length === 0 };
  });
}

// "3 ready · 2 need something · 1 skipped", for the wizard's footer.
export function tally(rows) {
  const ready = rows.filter((r) => r.ready).length;
  const skipped = rows.filter((r) => r.skip).length;
  const needing = rows.length - ready - skipped;
  return { ready, needing, skipped };
}

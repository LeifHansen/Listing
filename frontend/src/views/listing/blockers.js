/* What is stopping this listing from reaching eBay — and nothing else.

   A seller looking at a listing that won't publish has exactly one question:
   which field do I go fix? Every readiness surface in the app used to answer
   it from its own local heuristic — the editor's per-card completion map, the
   drafts strip's `missingRequired`, the bulk queue's "Needs info" pill — and
   they disagreed. A draft could show three cards "needing attention" and
   publish fine, or show none and be rejected by eBay for a title one
   character over. Answering the question well starts with answering it in one
   place.

   That place is here. These rules mirror the ERROR-level entries of
   backend/services/preflight.py — the server is the authority, and it has the
   final word at publish time — so anything flagged here really does stop a
   publish, and anything the server rejects for a reason a browser can see is
   flagged here first. What preflight also carries and this deliberately does
   NOT is advice: a missing description, a nicer photo. Those never appear in
   a blocker list, because a list that mixes "eBay will refuse this" with
   "this would sell better" is the thing that made the old UI unreadable.

   Account-level blockers (no payment policy, no ship-from location) live only
   on the server: they aren't in the listing, so the browser can't see them.
   The publish/preflight response carries those, and the fix-it panel renders
   them from the same {field, title, fix} shape used here. */

import { conditionLabel } from "@/lib/conditions";
import { specificValue } from "./specifics";

// eBay's own ceilings. Mirrors TITLE_MAX_CHARS / MAX_PHOTOS / EBAY_MIN_PRICE
// in the backend — keep the two in step.
export const TITLE_MAX = 80;
export const MAX_PHOTOS = 24;
export const EBAY_MIN_PRICE = 0.99;

// Does this publish include eBay? `targets` is the effectiveTargets array
// from publishShared (null = the legacy single-eBay path). eBay-only
// requirements — package weight, an eBay category, category item specifics —
// must not gate an Etsy-only publish: /api/publish only runs the providers it
// was given, so gating on them disabled the button for listings the backend
// would have accepted.
function wantsEbay(targets) {
  return !targets || !targets.length || targets.includes("ebay");
}

// The package weight in ounces, from the two fields a seller types it into.
export function weightOz(l = {}) {
  return (parseFloat(l.package_weight_lb) || 0) * 16
    + (parseFloat(l.package_weight_oz) || 0);
}

function aspectValue(l, name) {
  // ANY row for the aspect that carries a value, not merely the first row
  // with the name (specifics.js explains why the difference matters, and
  // which bug it caused). The server counts the aspect filled on exactly the
  // same rule, so this is the browser agreeing with the authority.
  const value = specificValue(l.item_specifics || [], name);
  // Brand mirrors the listing's own brand field (the Title card, the AI
  // identify pass and the maker check all write it there), so a required
  // Brand aspect isn't empty just because no specifics row exists for it.
  if (!value && String(name || "").trim().toLowerCase() === "brand") {
    return (l.brand || "").trim();
  }
  return value;
}

/* Every reason eBay would refuse this listing that a browser can see.

   Returns [{ key, target, label, why }] — `label` names the field for a chip,
   `why` says what's wrong in one line, and `target` is the jump anchor the
   editor's cards flag on (see fixTarget). Empty array = nothing in the
   listing is stopping a publish.

   `aspects` is the category's item-specific aspects when they're loaded
   (the editor has them; a listing card doesn't). Left null, the required-
   specifics rule is skipped rather than guessed at — a blocker list that
   invents blockers is worse than one that admits it can't see them.
   `conditions` is eBay's condition list for the category, on the same terms:
   null means nobody asked, never "eBay allows anything here".

   `mode` is which publish contract to check against, and it mirrors
   preflight.validate on the server:

     "live"   a NEW listing (or a relist, which is the same create call) —
              everything eBay demands before it will accept one.
     "revise" an edit to a listing eBay is ALREADY showing. Only the content
              this app is about to send has to be valid; the package weight
              and the category's required aspects are not resent and were
              settled when the listing went live — often years ago, often on
              eBay itself. Demanding them here blocked sellers out of editing
              listings that were live and selling, which is the whole reason
              the server draws this line. Keep the two in step. */
export function ebayBlockers(l = {},
  { targets = null, aspects = null, conditions = null, mode = "live" } = {}) {
  const revising = mode === "revise";
  const out = [];
  const add = (key, target, label, why) => out.push({ key, target, label, why });
  const ebay = wantsEbay(targets);

  // A listing with size/colour variations. This app has no variation model,
  // so it imported as one flat record with a single price and quantity — and
  // a revise would have sent an item-level Quantity into a structure eBay
  // says ReviseItem cannot revise, where a variation reaching zero is removed
  // from the listing. Flagged first and on its own: it is not a field to go
  // and fix, and letting the seller fill in the whole form before the server
  // refuses is the wrong order to find out.
  if (ebay && l.has_variations) {
    add("variations", null, "Variations",
      "This listing has size or colour variations. Thryft Shop can't edit "
      + "those yet — change it on eBay in Seller Hub instead.");
    return out;
  }

  const photos = (l.images || []).length || (l.image_urls || []).length;
  if (!photos) {
    add("photos", "photos", "Photos", "A listing needs at least one photo.");
  } else if ((l.images || []).length > MAX_PHOTOS) {
    add("photos", "photos", "Photos",
      `${(l.images || []).length} photos — eBay allows ${MAX_PHOTOS}.`);
  }

  const title = (l.title || "").trim();
  if (!title) {
    add("title", "title", "Title", "Every listing needs a title.");
  } else if (title.length > TITLE_MAX) {
    const over = title.length - TITLE_MAX;
    add("title", "title", "Title",
      `${over} character${over === 1 ? "" : "s"} over eBay's ${TITLE_MAX}-character limit.`);
  }

  if (!(l.condition || "").trim()) {
    add("condition", "condition", "Condition", "Pick the item's condition.");
  } else if (conditions && conditions.length
      && !conditions.some((c) => c.enum === l.condition)) {
    // eBay offers a different set of conditions per category — "Used - Good"
    // exists in media and nowhere else, the pre-owned grades only in apparel
    // — and refuses anything else with error 25021, after the whole publish.
    // The seller sees it here instead, next to the dropdown that fixes it.
    add("condition", "condition", "Condition",
      `eBay doesn't offer “${conditionLabel(l.condition)}” in this category — `
      + `pick one it does (${conditions.slice(0, 3)
        .map((c) => c.label || conditionLabel(c.enum)).join(", ")}…).`);
  }

  const fmt = String(l.listing_format || "FIXED_PRICE").toUpperCase();
  if (fmt === "AUCTION" || fmt === "AUCTION_BIN") {
    if (!(Number(l.auction_start_price) > 0)) {
      add("price", "price", "Starting bid", "An auction needs a starting bid.");
    }
    if (fmt === "AUCTION_BIN" && !(Number(l.price) > 0)) {
      add("bin_price", "price", "Buy It Now price",
        "This format needs a Buy It Now price too.");
    }
  } else {
    if (!(Number(l.price) > 0)) {
      add("price", "price", "Price", "Set what you're asking for it.");
    } else if (Number(l.price) < EBAY_MIN_PRICE) {
      add("price", "price", "Price",
        `eBay's minimum is $${EBAY_MIN_PRICE.toFixed(2)}.`);
    }
    if (!(Number(l.quantity ?? 1) >= 1)) {
      add("quantity", "price", "Quantity", "Quantity must be at least 1.");
    }
  }

  if (ebay) {
    const cid = String(l.category_id || "").trim();
    if (!cid) {
      add("category", "category", "eBay category",
        "eBay files every listing under a category.");
    } else if (!/^\d+$/.test(cid)) {
      add("category", "category", "eBay category",
        "Pick one from the suggestions — the ID fills itself in.");
    }
    if (!revising && !(weightOz(l) > 0)) {
      add("weight", "weight", "Package weight",
        "eBay prices shipping from the package weight.");
    }
    if (aspects && !revising) {
      const missing = aspects.filter(
        (a) => a.required && !aspectValue(l, a.name));
      if (missing.length) {
        const named = missing.slice(0, 3).map((a) => a.name).join(", ");
        add("specifics", "specifics", "Required item specifics",
          `${missing.length} required for this category: ${named}`
          + (missing.length > 3 ? "…" : ""));
      }
    }
  }
  return out;
}

// The blocking fields, named — "Title, Package weight". For the one-line
// summaries on cards and in tooltips.
export function blockerLabels(blockers) {
  return blockers.map((b) => b.label).join(", ");
}

/* What is stopping this listing from reaching eBay — and nothing else.

   A seller looking at a listing that won't publish has exactly one question:
   which field do I go fix? Every readiness surface in the app used to answer
   it from its own local heuristic — the editor's per-card completion map, the
   drafts grid's `missingRequired` and its amber "needs info" card — and
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

import {
  conditionLabel, descriptorProblems, descriptorsFor,
} from "@/lib/conditions";
import { specificValue } from "./specifics";

// eBay's own ceilings. Mirrors TITLE_MAX_CHARS / MAX_PHOTOS / EBAY_MIN_PRICE
// in the backend — keep the two in step.
export const TITLE_MAX = 80;
export const MAX_PHOTOS = 24;
// eBay allows ONE video per listing. It enforces that by ignoring the extras
// rather than refusing them, so the ceiling has to be kept on this side too:
// an upload that succeeds onto a listing that shows no video is the worst way
// to learn the rule. Mirrors models.MAX_VIDEOS — keep the two in step.
export const MAX_VIDEOS = 1;
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
export function ebayBlockers(listing = {},
  { targets = null, aspects = null, conditions = null, mode = "live" } = {}) {
  // A parameter default fires on `undefined` and NEVER on `null`, and a null
  // listing is a shape this app really produces: a bulk item whose draft did
  // not survive a restart used to carry one. Three of the four call sites
  // wrote `x.listing || {}` to cope with that; the fourth forgot, and a
  // single null took the whole bulk queue down to the error boundary --
  // every surviving draft in the batch with it. Normalizing here is what
  // makes remembering unnecessary.
  const l = listing || {};
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
  } else if (conditions && conditions.length) {
    // The second half of a trading card's condition. "Graded" needs a
    // grading service and a grade, "Ungraded" a card condition, and eBay
    // refuses a card that stops at the first answer. Which conditions carry
    // a second step, and what it offers, is eBay's answer for the category
    // (`descriptors` on each entry); a list without any checks nothing.
    // Mirrors preflight._check_condition_descriptors.
    const problems = descriptorProblems(
      l.condition_descriptors, descriptorsFor(conditions, l.condition));
    if (problems.length) {
      const { descriptor: d, problem } = problems[0];
      const cond = conditions.find((c) => c.enum === l.condition);
      const condLabel = (cond && cond.label) || conditionLabel(l.condition);
      const offered = (d.values || []).slice(0, 4).map((v) => v.name).join(", ");
      add("condition_descriptors", "condition", d.name,
        problem === "missing"
          ? `A ${condLabel} card needs its ${d.name}`
            + (offered ? ` (${offered}…).` : ".")
          : `eBay doesn't offer that ${d.name} here — pick one from its list`
            + (offered ? ` (${offered}…).` : "."));
    }
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

/* ---- Etsy -------------------------------------------------------------

   Every reason Etsy would refuse this listing that a browser can see: the
   ERROR-level rules of backend/marketplaces/mapping_etsy.preflight, on the
   same terms as ebayBlockers above (the server is the authority; a rule
   here is one it enforces). Etsy asks things eBay never did — a category
   of its own, who made the item and when, a shipping profile, a return
   policy and a processing profile — so an Etsy-only publish used to show
   "Ready" and be refused by the server for all five.

   `settings` is /api/etsy/settings-options (the shop's profiles and the
   account defaults), when it is loaded. Left null, the three profile rules
   are skipped rather than guessed at — the same "nobody asked" rule as
   `aspects` above. `mode` is "draft" | "live" | "revise": a draft may still
   leave the return policy and the photo for later, exactly as the server
   lets it. */

export const ETSY_MIN_PRICE = 0.20;
export const ETSY_TITLE_MAX = 140;
export const ETSY_MAX_PHOTOS = 10;
// Etsy's ceiling on words in capitals. Mirrors mapping_etsy.ALL_CAPS_WORD_LIMIT.
export const ETSY_ALL_CAPS_WORD_LIMIT = 3;
export const ETSY_WHO_MADE = ["i_did", "someone_else", "collective"];
// Etsy's when_made vocabulary; the top bucket is renamed every January.
// Mirrors mapping_etsy.WHEN_MADE — keep the two in step.
export const ETSY_WHEN_MADE = [
  "made_to_order", "2020_2026", "2010_2019", "2007_2009", "before_2007",
  "2000_2006", "1990s", "1980s", "1970s", "1960s", "1950s", "1940s",
  "1930s", "1920s", "1910s", "1900s", "1800s", "1700s", "before_1700",
];
export const ETSY_VINTAGE_YEARS = 20;

// The title as Etsy will accept it — mirrors mapping_etsy.clean_title, so
// the editor can show what Etsy gets and the crosspost review can offer it.
export function etsyTitle(title) {
  let text = String(title || "").replace(/[$^`]/g, "");
  const seen = new Set();
  let kept = "";
  for (const ch of text) {
    if ("%:&+".includes(ch)) {
      if (seen.has(ch)) continue;
      seen.add(ch);
    }
    kept += ch;
  }
  text = kept.replace(/\s+/g, " ").trim();
  while (text && !/^[\p{L}\p{N}]/u.test(text)) text = text.slice(1).replace(/^\s+/, "");
  let capitals = 0;
  const words = text.split(" ").map((word) => {
    const letters = word.replace(/[^\p{L}]/gu, "");
    if (letters.length >= 2 && letters === letters.toUpperCase()) {
      capitals += 1;
      if (capitals > ETSY_ALL_CAPS_WORD_LIMIT) {
        return word.replace(/[A-Za-z]+(?:'[A-Za-z]+)?/g,
          (run) => run[0].toUpperCase() + run.slice(1).toLowerCase());
      }
    }
    return word;
  });
  return words.join(" ").slice(0, ETSY_TITLE_MAX).replace(/\s+$/, "");
}

// The most recent year a when_made bucket can mean; null for made-to-order
// or anything outside Etsy's list. Mirrors mapping_etsy.when_made_latest_year.
export function whenMadeLatestYear(whenMade) {
  if (!ETSY_WHEN_MADE.includes(whenMade) || whenMade === "made_to_order") return null;
  if (whenMade.startsWith("before_")) return Number(whenMade.slice(7)) - 1;
  if (whenMade.includes("_")) return Number(whenMade.split("_")[1]);
  if (whenMade.endsWith("s")) return Number(whenMade.slice(0, -1)) + 9;
  return null;
}

export function isVintage(whenMade, year = new Date().getFullYear()) {
  const latest = whenMadeLatestYear(whenMade);
  return latest !== null && latest <= year - ETSY_VINTAGE_YEARS;
}

// Etsy's third kind of item, the one this app cannot fill in: "someone
// else made it", not vintage, not a supply — handmade with a production
// partner, which Etsy then wants named. Mirrors needs_production_partner.
export function needsProductionPartner(etsy = {}, year) {
  const e = etsy || {};
  return e.who_made === "someone_else" && !e.is_supply
    && ETSY_WHEN_MADE.includes(e.when_made) && !isVintage(e.when_made, year);
}

const digits = (value) => {
  const text = String(value ?? "").trim();
  return /^\d+$/.test(text) ? text : "";
};

// A per-listing override, else the account default from Settings — the
// same precedence as mapping_etsy.shipping_profile_for and friends.
function etsyProfile(l, settings, key) {
  return digits((l.etsy || {})[key])
    || digits(settings && settings.selected && settings.selected[key]);
}

const stripHtml = (text) => String(text || "").replace(/<[^>]+>/g, " ").trim();

export function etsyBlockers(listing = {},
  { settings = null, mode = "live" } = {}) {
  const l = listing || {};
  const e = l.etsy || {};
  const strict = mode !== "draft";
  const out = [];
  const add = (key, target, label, why) => out.push({ key, target, label, why });

  const fmt = String(l.listing_format || "FIXED_PRICE").toUpperCase();
  if (fmt !== "FIXED_PRICE") {
    add("format", "format", "Selling format",
      "Etsy doesn't do auctions — switch to Buy It Now, or unselect Etsy.");
  }
  if (l.has_variations) {
    add("variations", null, "Variations",
      "Etsy gets one price and one stock count from this app, and this listing "
      + "has sizes or colours with their own — list it on Etsy by hand.");
  }
  const photos = (l.images || []).length || (l.image_urls || []).length;
  if (!photos && strict) {
    add("photos", "photos", "Photos", "Etsy requires at least one photo.");
  }
  const title = (l.title || "").trim();
  if (!title) {
    add("title", "title", "Title", "Every listing needs a title.");
  } else if (!etsyTitle(title)) {
    add("title", "title", "Title",
      "An Etsy title has to start with a letter or a number — this one is only symbols.");
  }
  if (!stripHtml(l.description)) {
    add("description", "description", "Description", "Etsy requires a description.");
  }
  if (!(Number(l.price) >= ETSY_MIN_PRICE)) {
    add("price", "price", "Price", `Etsy's minimum is ${ETSY_MIN_PRICE.toFixed(2)}.`);
  }
  if (!(Number(e.taxonomy_id) > 0)) {
    add("etsy_taxonomy", "etsy_taxonomy", "Etsy category",
      "Pick an Etsy category on the Etsy card, or tap Suggest.");
  }
  if (!ETSY_WHO_MADE.includes(e.who_made) || !ETSY_WHEN_MADE.includes(e.when_made)) {
    add("etsy_attribution", "etsy_attribution", "Who & when made",
      `Etsy only allows handmade, vintage (${ETSY_VINTAGE_YEARS}+ years) and craft `
      + "supplies — say who made it and when, on the Etsy card.");
  } else if (needsProductionPartner(e)) {
    add("partner", "etsy_attribution", "Vintage or supply",
      `Something someone else made in the last ${ETSY_VINTAGE_YEARS} years needs a `
      + "production partner on Etsy — set when it was made to "
      + `${ETSY_VINTAGE_YEARS}+ years ago, tick craft supply, or choose “I did”.`);
  }
  if (settings) {
    if (!etsyProfile(l, settings, "shipping_profile_id")) {
      add("etsy_shipping_profile", "etsy_shipping_profile", "Etsy shipping profile",
        "Pick one on the Etsy card, or set a default under Settings → Etsy.");
    }
    if (!etsyProfile(l, settings, "readiness_state_id")) {
      add("etsy_readiness_state", "etsy_readiness_state", "Etsy processing time",
        "Etsy requires a processing profile on every listing — pick one on the "
        + "Etsy card, or set a default under Settings → Etsy.");
    }
    if (strict && !etsyProfile(l, settings, "return_policy_id")) {
      add("etsy_return_policy", "etsy_return_policy", "Etsy return policy",
        "Etsy requires a return policy before a listing goes live — pick one on "
        + "the Etsy card, or set a default under Settings → Etsy.");
    }
  }
  return out;
}

/* One list for the marketplaces this publish is going to: eBay's rules when
   eBay is a target (or when there is no selector — the legacy path), Etsy's
   when Etsy is. A field both refuse appears once, under eBay's wording,
   because that is the list every surface already knew how to render. */
export function blockersFor(listing, targets,
  { aspects = null, conditions = null, mode = "live", etsySettings = null } = {}) {
  const out = wantsEbay(targets)
    ? ebayBlockers(listing, { targets, aspects, conditions, mode })
    : [];
  if (targets && targets.includes("etsy")) {
    const seen = new Set(out.map((b) => b.key));
    for (const b of etsyBlockers(listing, { settings: etsySettings, mode })) {
      if (!seen.has(b.key)) out.push(b);
    }
  }
  return out;
}

// "eBay", "Etsy", "eBay and Etsy" — the marketplaces a publish is going to,
// in words, for every sentence that used to say "eBay" unconditionally.
export function marketNames(targets) {
  const keys = targets && targets.length ? targets : ["ebay"];
  const label = (k) => (k === "ebay" ? "eBay" : k.charAt(0).toUpperCase() + k.slice(1));
  const names = keys.map(label);
  return names.length > 1
    ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`
    : names[0];
}

// The one-line verdict a publish surface leads with: "3 things Etsy needs",
// "2 fields eBay won't accept", or the mixed form. Names the marketplaces
// that are actually being published to, never "eBay" by habit.
export function blockerHeadline(blockers, targets) {
  const n = blockers.length;
  if (!n) return "";
  const names = marketNames(targets);
  const many = n === 1 ? "1 field" : `${n} fields`;
  return `${many} ${names} won't accept`;
}

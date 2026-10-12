/* What the editor has changed, said field by field.

   Autosave (LISTING_REDESIGN.md, Phase 1) does not send the listing. It sends
   the keys whose value differs from what the server is known to hold, through
   `PATCH /api/listings/{id}` -- the door Phase 0 opened to the editor's
   fields, and the one place the server lets a client change one thing
   without a stale copy of everything else riding along.

   Two questions live here. Which fields may travel (the allow-list below,
   mirroring the backend's _PATCHABLE + _EDITOR_PATCHABLE -- keep the two in
   step), and when two values count as THE SAME. The second matters more
   than it looks: the form holds numbers as the strings typed, the server
   holds them as numbers, and "30" against 30 is not an edit. Treating it as
   one would save on every keystroke of a field nobody touched, and -- worse
   -- make the diff useless as a "has anything changed?" test for the Done
   button. */

// Mirrors backend/main.py `_PATCHABLE` (the card fields, any stage) ...
export const CARD_FIELDS = [
  "fulfillment_policy_id", "category_id", "category_suggestion",
  "price", "quantity", "condition", "condition_descriptors",
  "listing_format", "auction_start_price",
];

// ... and `_EDITOR_PATCHABLE` (the editor's fields, drafts only).
export const EDITOR_FIELDS = [
  "title", "subtitle", "brand", "description", "condition_description",
  "item_specifics",
  "package_weight_lb", "package_weight_oz",
  "package_length_in", "package_width_in", "package_height_in",
  "purchase_price", "retail_price", "promote", "ad_rate_percent",
  "auction_duration", "store_category_id", "store_category_name",
  "etsy", "depop", "currency",
];

export const AUTOSAVE_FIELDS = [...CARD_FIELDS, ...EDITOR_FIELDS];

const MONEY = new Set(["price", "purchase_price", "retail_price", "auction_start_price"]);
const MEASURE = new Set([
  "package_weight_lb", "package_weight_oz",
  "package_length_in", "package_width_in", "package_height_in", "ad_rate_percent",
]);
const STRUCTURED = new Set(["condition_descriptors", "item_specifics", "etsy", "depop"]);

const text = (v) => (v == null ? "" : String(v));

// Blank is "no price", not zero: the server stores None and the form "".
const asMoney = (v) => {
  const t = text(v).trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
};

const asMeasure = (v) => {
  const n = parseFloat(text(v));
  return Number.isFinite(n) ? n : 0;
};

// Keys in one order, recursively, so two objects that say the same thing
// stringify the same. The server's model_dump and the form's spread of
// EMPTY.etsy do not agree on key order, and must not read as a change.
function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") {
    return Object.keys(value).sort().reduce((o, k) => {
      o[k] = stable(value[k]);
      return o;
    }, {});
  }
  return value;
}

/** One field, in the shape the server stores it. */
export function wire(field, value) {
  if (MONEY.has(field)) return asMoney(value);
  if (MEASURE.has(field)) return asMeasure(value);
  if (field === "quantity") {
    const n = parseInt(text(value), 10);
    return Number.isFinite(n) && n > 0 ? n : 1;
  }
  if (field === "promote") return !!value;
  if (field === "item_specifics") {
    // The same rows collect() keeps: a name, a value, the AI's mark.
    return (value || [])
      .map((s) => ({ name: text(s?.name).trim(), value: text(s?.value).trim(),
                     confidence: text(s?.confidence) }))
      .filter((s) => s.name);
  }
  if (field === "condition_descriptors") return stable(value || []);
  if (STRUCTURED.has(field)) return stable(value || {});
  return text(value).trim();
}

/** Do these two values of `field` mean the same thing to the server? */
export function same(field, a, b) {
  const wa = wire(field, a);
  const wb = wire(field, b);
  if (wa !== null && typeof wa === "object") {
    return JSON.stringify(wa) === JSON.stringify(wb);
  }
  return wa === wb;
}

/**
 * The fields of `current` that differ from `saved`, each in wire form, and
 * nothing else. An empty object means the server already holds everything
 * the editor shows. `saved` may be null (nothing known) -- then every field
 * that is not at its blank value counts as a change.
 */
export function changedFields(current, saved, fields = AUTOSAVE_FIELDS) {
  const out = {};
  const base = saved || {};
  for (const f of fields) {
    if (!same(f, current?.[f], base[f])) out[f] = wire(f, current?.[f]);
  }
  return out;
}

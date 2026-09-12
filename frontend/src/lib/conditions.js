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
   editor show the right choices while the seller is still typing. */

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

/* --- the second step: eBay's condition DESCRIPTORS ---------------------------

   In the single-card categories (Sports 261328, CCG 183454, Non-Sport 183050)
   "Graded" or "Ungraded" is only half of a condition. eBay REQUIRES
   descriptors underneath: a graded card names its grading service and grade
   (and may give a certification number); an ungraded one names its card
   condition (Near Mint or Better / Excellent / Very Good / Poor, or the CCG
   played-ness ladder). A listing that stops at "Graded" is refused outright.

   Which conditions carry descriptors, and what each one offers, is eBay's
   answer for the category -- `descriptors` on each entry of the condition
   list (backend taxonomy.parse_condition_descriptors, which these mirror; the
   server is the authority). Nothing here hardcodes an id: eBay adds graders
   and moves ladders, and a stale id is a refused publish naming a number the
   seller cannot act on.

   A listing holds its answers as [{id, values, text, label, value_labels}]
   (models.ConditionDescriptor): eBay's descriptor id, the value id(s) picked,
   free text where the descriptor takes it, and eBay's wording for both so a
   card can say "PSA 10" without a lookup. */

// The descriptors eBay defines for `condition` in this category. Empty for
// every condition but a trading card's, and for a list nobody asked for.
export function descriptorsFor(conditions, condition) {
  const cond = String(condition || "").trim().toUpperCase();
  const hit = (conditions || []).find(
    (c) => String(c?.enum || "").toUpperCase() === cond);
  return (hit && Array.isArray(hit.descriptors)) ? hit.descriptors : [];
}

// Does this category ask a second question for any of its conditions?
export function hasDescriptors(conditions) {
  return (conditions || []).some((c) => (c?.descriptors || []).length > 0);
}

const asList = (v) => (v == null || v === "" ? [] : Array.isArray(v) ? v : [v]);

/* The listing's descriptors, kept to what the condition offers.

   A descriptor the condition doesn't define is dropped (a grade left over
   from before the seller switched the card to Ungraded), a value id the
   descriptor doesn't list is dropped, free text is clipped to eBay's length,
   and every label is refreshed to eBay's current wording. Order follows
   eBay's. `meta` empty means the condition takes no descriptors, and the
   answer is none. Mirrors taxonomy.fit_condition_descriptors. */
export function fitDescriptors(descriptors, meta) {
  const byId = new Map();
  for (const d of descriptors || []) {
    const id = String(d?.id || "").trim();
    if (id && !byId.has(id)) byId.set(id, d);
  }
  const out = [];
  for (const m of meta || []) {
    const d = byId.get(m.id);
    if (!d) continue;
    if (m.free_text) {
      let text = String(d.text || "").trim();
      if (m.max_length) text = text.slice(0, m.max_length);
      if (!text) continue;
      out.push({ id: m.id, values: [], text, label: m.name, value_labels: [] });
      continue;
    }
    const names = new Map((m.values || []).map((v) => [v.id, v.name]));
    let kept = asList(d.values).map((v) => String(v).trim()).filter((v) => names.has(v));
    if ((m.cardinality || "SINGLE") !== "MULTI") kept = kept.slice(0, 1);
    if (!kept.length) continue;
    out.push({ id: m.id, values: kept, text: "", label: m.name,
      value_labels: kept.map((v) => names.get(v)) });
  }
  return out;
}

/* What eBay would refuse about a listing's descriptors: [{descriptor,
   problem}] with problem "missing" (a required descriptor has no answer) or
   "not_offered" (an answer eBay doesn't list). Empty when the condition takes
   none, or every one is in order. Mirrors taxonomy.condition_descriptor_problems. */
export function descriptorProblems(descriptors, meta) {
  const byId = new Map();
  for (const d of descriptors || []) {
    const id = String(d?.id || "").trim();
    if (id && !byId.has(id)) byId.set(id, d);
  }
  const problems = [];
  for (const m of meta || []) {
    const d = byId.get(m.id);
    if (m.free_text) {
      if (m.required && !String(d?.text || "").trim()) {
        problems.push({ descriptor: m, problem: "missing" });
      }
      continue;
    }
    const offered = new Set((m.values || []).map((v) => v.id));
    const chosen = asList(d?.values).map((v) => String(v).trim()).filter(Boolean);
    if (!chosen.length) {
      if (m.required) problems.push({ descriptor: m, problem: "missing" });
    } else if (chosen.some((v) => !offered.has(v))) {
      problems.push({ descriptor: m, problem: "not_offered" });
    }
  }
  return problems;
}

// The listing's descriptors with one answer changed: a value id for a
// pick-one descriptor, free text for the other kind. "" clears it. The
// result is fitted, so it is always in eBay's order and wording.
export function withDescriptor(descriptors, meta, descriptor, value) {
  const rest = (descriptors || []).filter((d) => String(d?.id) !== String(descriptor.id));
  const entry = descriptor.free_text
    ? { id: descriptor.id, text: String(value || "") }
    : { id: descriptor.id, values: value ? [String(value)] : [] };
  return fitDescriptors([...rest, entry], meta);
}

// Same answers? Ids and text decide; labels are display (the server ignores
// them the same way when it decides what the seller edited). `labels: true`
// compares the wording too -- what the editor and the bulk queue ask before
// replacing a listing's copy with the fitted one, so an imported card's bare
// ids pick up eBay's names exactly once and a fitted copy is left alone.
export function sameDescriptors(a, b, { labels = false } = {}) {
  const key = (list) => JSON.stringify((list || []).map((d) => [
    String(d?.id || ""), asList(d?.values).map(String), String(d?.text || ""),
    ...(labels ? [String(d?.label || ""), asList(d?.value_labels).map(String)] : []),
  ]));
  return key(a) === key(b);
}

/* One line for a card or a pill: "Graded · PSA · 10 · #12345678", or just
   the condition where there is no second step. eBay's own label for the
   condition when the category's list is to hand, the generic one otherwise. */
export function conditionSummary(l, conditions) {
  const cond = String(l?.condition || "").trim().toUpperCase();
  if (!cond) return "";
  const hit = (conditions || []).find((c) => String(c?.enum || "").toUpperCase() === cond);
  const parts = [hit?.label || conditionLabel(cond)];
  for (const d of l?.condition_descriptors || []) {
    const labels = asList(d?.value_labels).map((v) => String(v).trim()).filter(Boolean);
    const values = asList(d?.values).map((v) => String(v).trim()).filter(Boolean);
    if (labels.length) parts.push(labels.join(" / "));
    else if (values.length) parts.push(values.join(", "));
    if (String(d?.text || "").trim()) parts.push(`#${String(d.text).trim()}`);
  }
  return parts.join(" · ");
}

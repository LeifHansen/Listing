/* Item-specifics list transforms, kept pure so they're testable without a
   renderer — the same split as blockers.js.

   item_specifics is a flat list of {name, value, confidence} rows, and one
   aspect can legitimately own SEVERAL of them: eBay's multi-select specifics
   (Features, Style, Season...) are tick boxes, not dropdowns. */

const key = (s) => (s || "").trim().toLowerCase();

// Every value held under one aspect name, in list order.
export function specificValues(specifics, name) {
  return specifics
    .filter((s) => key(s.name) === key(name))
    .map((s) => (s.value || "").trim())
    .filter(Boolean);
}

// Tick / untick one value of a multi-select aspect. Ticking ADDS a row instead
// of replacing one — that's the whole difference between eBay's checkbox
// specifics and its dropdowns, and why they only ever held one answer before.
// Returns the array unchanged when nothing moves, so React can skip the render.
export function toggleSpecificValue(specifics, name, value, on) {
  const same = (s) => key(s.name) === key(name);
  const hit = (s) => same(s) && key(s.value) === key(value);
  if (!on) {
    return specifics.some(hit) ? specifics.filter((s) => !hit(s)) : specifics;
  }
  if (specifics.some(hit)) return specifics;
  const next = [...specifics];
  // Reuse the aspect's empty row if one is lying around (clearing a value
  // leaves one behind) rather than accumulating blanks.
  const i = next.findIndex((s) => same(s) && !(s.value || "").trim());
  const row = { name, value, confidence: "" };
  if (i >= 0) next[i] = row;
  else next.push(row);
  return next;
}

/* How many specifics still want a glance from the seller.

   Counts ASPECTS, not rows. A multi-select aspect holds one row per ticked
   value but shows ONE review flag for the whole group, and one ✓ clears the
   group (confirmSpecificRows below) — so four AI-ticked values are one thing
   to look at, not four. Counting rows made the editor's banner claim "4 AI
   guesses to check" where a single flag was on screen, and made one click
   drop the count by four. */
export function reviewAspectCount(specifics) {
  const names = new Set();
  for (const s of specifics || []) {
    if ((s.value || "").trim() && s.confidence === "medium") names.add(key(s.name));
  }
  return names.size;
}

// Clear the AI review flag on EVERY row for an aspect, not just the first: a
// multi-select aspect shows one flag for the whole group, so one ✓ clears it.
export function confirmSpecificRows(specifics, name) {
  if (!specifics.some((s) => key(s.name) === key(name))) return specifics;
  return specifics.map(
    (s) => (key(s.name) === key(name) ? { ...s, confidence: "" } : s));
}

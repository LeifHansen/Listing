/**
 * Suggestions the seller has waved away.
 *
 * "Suggested actions" is a to-do list the recommendation engine rebuilds from
 * scratch on every load, so a row the seller has decided against comes back
 * every time — and a list that will not shrink stops being read at all. The
 * dismiss control on each row is what makes it a list they own; this is where
 * that decision is kept.
 *
 * A dismissal is per LISTING and per SUGGESTION TYPE: waving away "Lower the
 * price" on one item leaves its "Add more photos" alone, and leaves every
 * other listing's price suggestion alone too.
 *
 * It lives in this browser, not on the account. That is a real limit — a
 * seller who dismisses on their phone still sees the row on their laptop —
 * and it is the honest shape for what this is: a display preference about a
 * list that is recomputed on every load, not a fact about the listing. The
 * alternative is a table of per-suggestion rows on the server, which is a
 * much larger thing to own for something the seller can undo in one tap
 * (see the "Restore dismissed" control on the section header).
 *
 * The stored list is BOUNDED. It is written once per dismissal and never
 * pruned by anything else, so on a big store it would otherwise grow without
 * limit inside a storage quota shared with the theme, the sold-range picker
 * and the id of a running bulk batch — and the browser evicts by throwing on
 * the next write, not by dropping the oldest key. Newest wins.
 */
import { readLocal, writeLocal } from "./localPrefs";

const KEY = "dismissed-recs";     // see lib/localPrefs for the storage name
// How many dismissals are remembered. Past this the oldest fall off and their
// suggestion comes back, which is the mild failure of the two: the list is
// capped at 50 by the API, so this is many screens' worth of decisions.
const MAX = 300;

/** The identity of one suggestion: this listing, this kind of advice. */
export function recKey(rec) {
  return `${rec.listing_id}|${rec.type}`;
}

/** Everything dismissed in this browser, newest last. */
export function readDismissed() {
  try {
    const raw = readLocal(KEY);
    if (!raw) return [];
    const list = JSON.parse(raw);
    return Array.isArray(list) ? list.filter((k) => typeof k === "string") : [];
  } catch (e) {
    return [];   // unreadable or not ours — nothing is dismissed
  }
}

/** `list` with `rec` dismissed, saved. Returns the new list. */
export function dismiss(list, rec) {
  const key = recKey(rec);
  const next = [...list.filter((k) => k !== key), key].slice(-MAX);
  try { writeLocal(KEY, JSON.stringify(next)); } catch (e) { /* this session only */ }
  return next;
}

/** Bring every dismissed suggestion back. Returns the new (empty) list. */
export function restoreAll() {
  try { writeLocal(KEY, "[]"); } catch (e) { /* this session only */ }
  return [];
}

/** `recs` minus the dismissed ones. */
export function withoutDismissed(recs, list) {
  if (!list.length) return recs;
  const gone = new Set(list);
  return recs.filter((r) => !gone.has(recKey(r)));
}

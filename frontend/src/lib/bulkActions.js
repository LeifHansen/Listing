/* What the Manage tab's selection bar says about a run, before and after.
 *
 * Every bulk action on the bar is a job that reports {changed, skipped,
 * failed, results}; every one of them toasted that report the same way, in a
 * copy of the same eleven lines. One copy now. */

import { reasonLines } from "@/lib/bulkReport";
import { keptWhenEnded } from "@/lib/listingsView";

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/* The toast for a finished run. `did(n)` says what happened to the changed
   ones ("Relisted 3 listings with fresh copy"); `empty` is the line when
   nothing was. Returns [message, options] for toast(). */
export function bulkToast(res, { did, empty = "Nothing to change." }) {
  const parts = [];
  if (res.changed) parts.push(did(res.changed, res));
  if (res.skipped) parts.push(`${res.skipped} skipped`);
  if (res.failed) parts.push(`${res.failed} failed`);
  const results = res.results || {};
  const lines = reasonLines([...(results.failed || []), ...(results.skipped || [])]);
  return [
    [parts.join(" · ") || empty, ...lines].join("\n"),
    {
      kind: res.changed ? "success" : res.failed ? "error" : "info",
      ttl: lines.length ? 12000 : undefined,
    },
  ];
}

/* The confirm for "End listings": what will become of the cards, which is
   not the same for every listing — a copy the store sync made is removed on
   the spot, a listing made here is kept under Inactive for the grace period
   (lib/listingsView.keptWhenEnded mirrors the server's rule). */
export function endingSummary(items, graceDays) {
  const kept = items.filter(keptWhenEnded).length;
  const removed = items.length - kept;
  const n = items.length;
  const head = n === 1
    ? `"${items[0].listing?.title || items[0].title || "This listing"}" comes off eBay immediately.`
    : `${plural(n, "listing")} come off eBay immediately.`;
  const fate = [];
  if (kept) {
    fate.push(`${n === 1 ? "It moves" : `${kept} move`} to Inactive, where you `
      + `can relist ${kept === 1 ? "it" : "them"} — we clear ${kept === 1 ? "it" : "them"} `
      + `out after ${graceDays} days.`);
  }
  if (removed) {
    fate.push(`${n === 1 ? "Its card is" : `${removed} card${removed === 1 ? " is" : "s are"}`} `
      + "removed from here — "
      + `${removed === 1 ? "that one is a copy" : "those are copies"} of your eBay `
      + "listing, with nothing of yours in it to keep.");
  }
  return [head, ...fate].join(" ");
}

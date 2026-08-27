/* The seller's last background-removal choice.
 *
 * "Remove background & replace with white" is a checkbox on the uploader, and
 * it was local to that screen — so "Add photos" on a listing the seller had
 * already cleaned up sent nothing, the server defaulted it to false, and the
 * new photos went in with their original backgrounds next to cut-out ones. No
 * toggle offered, no message afterwards; on a dark backdrop the difference is
 * the whole point of the feature.
 *
 * A per-device viewing preference, so it rides localStorage next to the theme
 * and the listings layout rather than the server. Every accessor is guarded:
 * private mode and storage-blocked browsers throw on read.
 */
const KEY = "quickflip-remove-bg";

export function lastRemoveBg() {
  try { return localStorage.getItem(KEY) === "yes"; } catch (e) { return false; }
}

export function rememberRemoveBg(on) {
  try { localStorage.setItem(KEY, on ? "yes" : "no"); } catch (e) {}
}

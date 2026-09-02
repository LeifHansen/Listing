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
import { readLocal, writeLocal } from "./localPrefs";

const KEY = "remove-bg";   // see lib/localPrefs

export function lastRemoveBg() {
  return readLocal(KEY) === "yes";
}

export function rememberRemoveBg(on) {
  writeLocal(KEY, on ? "yes" : "no");
}

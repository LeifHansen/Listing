/* The seller's last background-removal choice, so the uploader's checkbox
 * opens where they left it instead of unticked every time.
 *
 * That is ALL it does. It was also read by "Add photos", to give a listing's
 * new photos the same treatment its first ones got — which meant adding a
 * photo silently replaced its background, from a checkbox on another screen
 * that the seller had last touched on a different pile. It seeds the
 * uploader's own toggle and nothing else now; see useListingForm.addImages.
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

/**
 * Browser-stored preferences, under the app's current name.
 *
 * The keys were minted as `quickflip-*` and the product is Thryft Shop, so
 * the audit asks for the rename. What it also asks for, and what makes this a
 * module rather than a find-and-replace, is that the rename must not throw a
 * seller's settings away: renaming the key alone silently resets their theme,
 * their list/grid choice, their remove-background default and their publish
 * targets on the first load after the release — and `bulk` holds the id of a
 * RUNNING batch, so losing it strands a job the app is still processing with
 * nothing on screen to watch it.
 *
 * So a read tries the current key, falls back to the old one, and — when the
 * old one answered — writes the value forward and removes it. One load per
 * browser and the old key is gone; nobody notices anything.
 *
 * Every call is wrapped: `localStorage` THROWS (not returns null) in Safari
 * with site data blocked and in an iOS WKWebView without storage, which this
 * app ships to. A preference that cannot be read is the default, never an
 * error — and never, as the AI-consent bug showed, a "yes".
 */
const NEW = (name) => `thryft-${name}`;
const OLD = (name) => `quickflip-${name}`;

function get(key) {
  try {
    return localStorage.getItem(key);
  } catch (e) {
    return null;   // storage refused — the caller's default stands
  }
}

export function readLocal(name) {
  const value = get(NEW(name));
  if (value !== null) return value;
  const legacy = get(OLD(name));
  if (legacy === null) return null;
  // Carry it forward, then drop the old one. Best-effort: a browser that
  // refuses the write still gets the value back, it just migrates next time.
  try {
    localStorage.setItem(NEW(name), legacy);
    localStorage.removeItem(OLD(name));
  } catch (e) { /* read-only storage — the fallback above still answered */ }
  return legacy;
}

export function writeLocal(name, value) {
  try {
    localStorage.setItem(NEW(name), value);
    // A stale legacy copy would win nothing (reads prefer the new key), but
    // leaving it behind means the migration never finishes for this browser.
    localStorage.removeItem(OLD(name));
  } catch (e) { /* private mode — the preference lasts this session only */ }
}

export function clearLocal(name) {
  try {
    localStorage.removeItem(NEW(name));
    localStorage.removeItem(OLD(name));
  } catch (e) { /* nothing to do */ }
}

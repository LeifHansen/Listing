/* Reload once onto a new build when a lazily loaded screen has gone missing.
 *
 * The admin panels, the crosspost wizard and the inbox load on first use, from
 * files named by their content hash. A tab left open across a deploy still
 * asks for the old names, which the new image no longer serves, and Vite
 * reports that as `vite:preloadError` — which, unhandled, reached the error
 * boundary and replaced the whole app with its error card. A reload fetches
 * the new index and its new names.
 *
 * Once per minute at most: if the file is missing for some other reason, the
 * second failure is let through to the boundary instead of reloading forever.
 * (The native shell bundles every file, so this only ever fires on the web.)
 */
const KEY = "thryft-stale-build-reload";
const WINDOW_MS = 60_000;

export function installStaleBuildReload(win = window) {
  const onError = (event) => {
    try {
      const last = Number(win.sessionStorage.getItem(KEY) || 0);
      if (Date.now() - last < WINDOW_MS) return;
      win.sessionStorage.setItem(KEY, String(Date.now()));
    } catch (e) {
      return; // no storage, no loop guard: let the boundary handle it
    }
    event.preventDefault();
    win.location.reload();
  };
  win.addEventListener("vite:preloadError", onError);
  return () => win.removeEventListener("vite:preloadError", onError);
}

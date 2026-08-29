import { apiUrl, isNative, openExternal } from "@/lib/platform";

/* A link to a page the SERVER renders — /privacy-policy, /terms, /about.
 *
 * These are the pages Apple requires to be reachable from inside the app, and
 * a bare relative href cannot reach them there. The native shell bundles
 * `dist/` and serves it from capacitor://localhost with no server.url, so
 * href="/privacy-policy" resolves against that origin — where the file does
 * not exist (the routes live on FastAPI, and the bundle only has
 * privacy-policy.html). Every one of them 404'd inside the app, in the three
 * places whose own comments say they are the only way to reach the policies.
 *
 * apiUrl() points at the deployed API, and on native the system browser opens
 * it: openExternal falls back to window.open and reports failure rather than
 * navigating the webview away from the app with no way back.
 */
export function SiteLink({ path, className, children }) {
  const href = apiUrl(path);
  return (
    <a href={href} target="_blank" rel="noreferrer" className={className}
      onClick={(e) => {
        if (isNative()) { e.preventDefault(); openExternal(href); }
      }}>
      {children}
    </a>
  );
}

/* Download the seller's whole store as a spreadsheet.
 *
 * The file is built by the server (GET /api/listings/export.csv, and
 * backend/services/listing_export.py says what is in it and why). What lives
 * here is the part that is genuinely the client's problem:
 *
 *  - the request has to go through `api()` rather than a plain link. The web
 *    build authenticates with an httponly cookie, but the iOS/Android shell
 *    runs on capacitor://localhost and authenticates with a bearer token from
 *    the Keychain — a bare <a href> carries neither, so the "download" would
 *    be a 401 page saved as a .csv;
 *  - the browser has to be TOLD to save it. On the web that is an anchor with
 *    a download attribute; inside the native shell that does nothing at all,
 *    and the share sheet ("Save to Files") is the way a file leaves the app;
 *  - and the answer's headers say how many listings the store holds, so a
 *    file that was cut short can say so instead of looking complete.
 */
import { api } from "@/lib/api";
import { isNative } from "@/lib/platform";

export const EXPORT_PATH = "/api/listings/export.csv";
export const EXPORT_MIME = "text/csv";

// What the file is called when the server's Content-Disposition can't be read
// — an older proxy that drops the header, or a native fetch whose CORS
// exposure was missed. Same shape the server uses, so the two never look like
// different features.
export function defaultExportName(now = new Date()) {
  const day = Number.isNaN(now?.getTime?.()) ? new Date() : now;
  return `thryft-listings-${day.toISOString().slice(0, 10)}.csv`;
}

/* The filename out of a Content-Disposition header.
 *
 * Both forms, because both are served in the wild: RFC 5987's `filename*=`
 * (which wins where it appears — it is the one that can carry a non-ASCII
 * name) and the plain quoted `filename=`. Anything with a path separator in
 * it is ignored rather than cleaned: the header is the server's suggestion,
 * and a suggestion that tries to name a directory is not one worth honouring.
 */
export function filenameFrom(disposition, fallback = defaultExportName()) {
  const header = String(disposition || "");
  const extended = /filename\*\s*=\s*[^']*'[^']*'([^;]+)/i.exec(header);
  const plain = /filename\s*=\s*"([^"]+)"|filename\s*=\s*([^;]+)/i.exec(header);
  let name = "";
  if (extended) {
    try {
      name = decodeURIComponent(extended[1].trim());
    } catch (e) {
      name = extended[1].trim();
    }
  } else if (plain) {
    name = (plain[1] || plain[2] || "").trim();
  }
  if (!name || name.includes("/") || name.includes("\\")) return fallback;
  return name;
}

/* Hand the file to the platform, so the seller ends up with it somewhere.
 *
 * Returns "shared" or "downloaded" — the caller says something different for
 * each, because they end in different places — or null when the seller
 * dismissed the share sheet, which is a decision and not a failure.
 *
 * The share path is tried on the native shell ONLY. On the web,
 * navigator.share is a worse answer than a download: it is a dialog in the
 * way of a file the browser would otherwise just save.
 */
export async function saveFile(blob, filename) {
  if (isNative() && typeof navigator !== "undefined" && navigator.share) {
    try {
      const file = new File([blob], filename, { type: EXPORT_MIME });
      // canShare is the only reliable way to ask whether FILES are supported;
      // navigator.share exists on platforms that take text and nothing else,
      // and calling it with a file there rejects after the sheet has opened.
      if (!navigator.canShare || navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title: filename });
        return "shared";
      }
    } catch (e) {
      // AbortError is the seller tapping Cancel. Anything else means the
      // sheet could not be used, and the download below is still worth a try.
      if (e?.name === "AbortError") return null;
    }
  }
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.rel = "noopener";
    // Appended before the click: Firefox ignores a click on an anchor that is
    // not in the document.
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    // Not immediately — revoking before the browser has started reading the
    // blob cancels the download in Safari. The next tick is enough.
    setTimeout(() => {
      try { URL.revokeObjectURL(url); } catch (e) { /* already gone */ }
    }, 0);
  }
  return "downloaded";
}

/* Fetch the export and save it.
 *
 * Resolves with what the seller should be told: how the file left the app,
 * what it is called, how many listings the store holds, and — when the store
 * was bigger than one download may carry — how many of them actually made it.
 * Throws with the server's own sentence on a failure, which is what every
 * other call in this app does and what the toast expects.
 */
export async function exportListingsCsv() {
  const res = await api(EXPORT_PATH, { as: "response" });
  const blob = await res.blob();
  const filename = filenameFrom(res.headers.get("Content-Disposition"));
  const how = await saveFile(blob, filename);
  return {
    how,
    filename,
    total: headerCount(res.headers.get("X-Export-Total")),
    // Present only when the store is bigger than one export may carry, and
    // its value is how many rows were actually written.
    exported: headerCount(res.headers.get("X-Export-Truncated")),
  };
}

/* A count from a header, or undefined.
 *
 * An absent header has to come back as undefined, not 0: `Number(null)` is 0,
 * and the server omits X-Export-Total whenever it could not count the store.
 * A 0 there would be reported to the seller as "Exported 0 listings" about a
 * file with their whole inventory in it — a number nobody measured, presented
 * as one that was.
 */
function headerCount(raw) {
  if (raw == null || String(raw).trim() === "") return undefined;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : undefined;
}

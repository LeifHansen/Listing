import { apiUrl } from "@/lib/platform";

// Reporting a browser crash to the server.
//
// Until this existed the frontend was completely dark: no error boundary, no
// window.onerror, no unhandledrejection handler, and no ingest route. A React
// render crash was a white screen that reached nobody — not a log, not a
// toast, not a metric. The server saw a page load and then silence.
//
// The hard part is not sending the report. It is not turning one broken page
// into a flood, and never letting the reporter become the thing being
// reported. Four guards, because a loop here fires at render speed:
//
//  1. A per-page cap. A component throwing in a render loop can raise 60
//     errors a second; the cap makes that one POST.
//  2. A per-fingerprint set, which also absorbs React StrictMode's deliberate
//     double render in development.
//  3. An explicit skip for anything whose own stack names the ingest path.
//  4. Silence on failure — a failed report is dropped, never logged, never
//     toasted, never retried. Logging it is precisely what would feed it back
//     into the handlers below.
//
// It deliberately does NOT go through lib/api. That wrapper throws on failure
// and dispatches `auth:expired` on a 401 and `tokens:needed` on a 402 — any of
// which, reached from an error handler, re-enters the app while it is already
// failing. This uses bare fetch, and sendBeacon when the page is going away.

const INGEST = "/api/client-errors";
const MAX_REPORTS_PER_PAGE = 5;
const MAX_STACK = 8000;

let sent = 0;
let installed = false;
const seen = new Set();

// The X-Request-Id of the most recent API response. A crash usually follows a
// request; carrying its reference is what joins this report to the server-side
// row for the same moment. Cheap: one header read per response.
let lastRequestId = "";

export function noteRequestId(id) {
  if (id) lastRequestId = String(id).slice(0, 32);
}

// Message plus the first stack frame. Enough to tell two different crashes
// apart, stable enough that the same one repeating is recognised as itself.
function fingerprint(kind, message, stack) {
  const frame = String(stack || "").split("\n")[1] || "";
  return `${kind}|${message}|${frame.trim()}`;
}

function isOurOwn(stack) {
  return String(stack || "").includes(INGEST);
}

export function reportClientError(kind, error, extra = {}) {
  try {
    if (sent >= MAX_REPORTS_PER_PAGE) return false;

    const message = String(
      (error && (error.message || error.reason)) || error || "unknown",
    ).slice(0, 500);
    const stack = String((error && error.stack) || "").slice(0, MAX_STACK);
    if (isOurOwn(stack)) return false;

    const print = fingerprint(kind, message, stack);
    if (seen.has(print)) return false;
    seen.add(print);
    sent += 1;

    const body = JSON.stringify({
      kind,
      name: String((error && error.name) || "").slice(0, 120),
      message,
      stack,
      // Component NAMES survive minification even though line numbers do not,
      // so this is the part of a React crash that still says which screen.
      component_stack: String(extra.componentStack || "").slice(0, 4000),
      url: String(window.location && window.location.pathname).slice(0, 200),
      build: String(import.meta.env.VITE_BUILD_SHA || "").slice(0, 40),
      request_id: lastRequestId,
    });

    const url = apiUrl(INGEST);
    // The page may be unloading (an error during navigation); sendBeacon is
    // the only thing guaranteed to survive that.
    if (extra.unloading && navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
      return true;
    }
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
      credentials: "omit",
    }).catch(() => {});
    return true;
  } catch {
    // A reporter that throws is strictly worse than one that stays quiet.
    return false;
  }
}

export function installClientErrorReporting() {
  if (installed || typeof window === "undefined") return;
  installed = true;

  window.addEventListener("error", (event) => {
    reportClientError("window.onerror", event.error || event.message);
  });
  window.addEventListener("unhandledrejection", (event) => {
    reportClientError("unhandledrejection", event.reason);
  });
}

// Test seam. The module holds per-page state by design — a fresh page load is
// what resets it in the browser — so tests need a way to say "new page".
export function _resetForTests() {
  sent = 0;
  installed = false;
  lastRequestId = "";
  seen.clear();
}

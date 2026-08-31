// Pure logic for the operator console — kept out of the components so the
// dash-not-zero rules and the label maps are unit-testable without a DOM.

// The Overview window. Mirrors SOLD_RANGES' shape (lib/sales.js) but speaks
// in days because that is what /api/admin/overview takes.
export const ADMIN_RANGES = [
  { id: "7", days: 7, label: "7 days" },
  { id: "30", days: 30, label: "30 days" },
  { id: "90", days: 90, label: "90 days" },
];

// The console's one rendering rule, same as the dashboard tiles: a number
// nobody could measure is a dash, never a zero. `state` is a read-state
// object ({ kind: "loading" | "unavailable" | "ready", data }) and `pick`
// maps the loaded data to the number.
export function tileValue(state, pick) {
  if (state.kind !== "ready") return "—";
  const v = pick(state.data);
  return v == null ? "—" : v;
}

// The sub-line under a tile: silent while loading, honest during an outage.
export function tileSub(state, ready) {
  if (state.kind === "unavailable") return "we couldn’t check";
  if (state.kind !== "ready") return "";
  return typeof ready === "function" ? ready(state.data) : (ready || "");
}

// "12 sales · 2 at the asking price · more than one currency" — the sold
// tile's sub-line. Same phrasing rules as the dashboard's soldSubline: an
// estimate is labeled, a currency mix is named rather than summed silently.
export function salesSubline(sales) {
  if (!sales) return "";
  const parts = [`${sales.count} sale${sales.count === 1 ? "" : "s"}`];
  if (sales.approx) parts.push(`${sales.approx} at the asking price`);
  if (sales.mixed_currency) parts.push("more than one currency");
  if (sales.undated) parts.push(`${sales.undated} undated`);
  return parts.join(" · ");
}

// Audit actions as English. An unknown slug falls through readably (the
// slug itself), so a new backend action never renders as a blank row.
const ACTION_LABELS = {
  grant_tokens: "Granted tokens",
  revoke_sessions: "Forced sign-out",
  disable_account: "Disabled account",
  enable_account: "Re-enabled account",
  run_compliance_queue: "Ran compliance queue",
  grant_superadmin: "Granted superadmin",
  revoke_superadmin: "Revoked superadmin",
};

export function actionLabel(action) {
  return ACTION_LABELS[action] || String(action || "").replaceAll("_", " ");
}

const ACTION_TONES = {
  grant_tokens: "green",
  revoke_sessions: "yellow",
  disable_account: "red",
  enable_account: "green",
  run_compliance_queue: "blue",
  grant_superadmin: "red",
  revoke_superadmin: "red",
};

export function actionTone(action) {
  return ACTION_TONES[action] || "neutral";
}

// Compact relative time for console rows ("3h ago"). Falls back to the date
// past a week — an operator scanning the audit trail wants the calendar day
// there, not "41d ago" arithmetic.
export function relTime(iso, now = Date.now()) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.floor((now - t) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)}d ago`;
  return new Date(t).toLocaleDateString();
}

// Signed token amounts, spends negative: "+50", "−12". The MINUS SIGN, not
// a hyphen, matching the dashboard's profit line.
export function signedTokens(n) {
  const v = Number(n) || 0;
  return v < 0 ? `−${Math.abs(v)}` : `+${v}`;
}

// De-dupe for "load more" pages, same job as loadMoreListings' merge: a row
// written between two page loads can appear on both sides of the cursor.
export function mergeRows(existing, more) {
  const seen = new Set(existing.map((r) => r.id));
  return [...existing, ...more.filter((r) => !seen.has(r.id))];
}

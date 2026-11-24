/* Sale events (eBay markdown sales) on the client: which state each is in,
 * which listing is in which, and what a card shows for it.
 *
 * The server keeps the record (backend/services/sale_events) and every write
 * answers with the whole list, which the store adopts. Nothing here is ever
 * written onto a listing: which listing is on sale is worked out by joining
 * the sales' eBay item ids to the listings on screen, so a sale starting or
 * ending never rewrites — or reorders — a card.
 *
 * "Sale event" in the code, "sale" on screen: lib/sales.js already means
 * SOLD items, and the two must never be confused in a name. */

export const DISCOUNT_CHOICES = [10, 15, 20, 25, 30, 40, 50];
export const DURATION_CHOICES = [3, 7, 14, 30];

// What the server reports as eBay's bounds, before it has answered.
export const DEFAULT_LIMITS = Object.freeze({
  min_percent: 5, max_percent: 80, max_days: 45, max_listings: 500, name_max: 90,
});

const time = (iso) => {
  const t = Date.parse(iso || "");
  return Number.isNaN(t) ? null : t;
};

/* draft | scheduled | running | paused | ended — by the dates as much as by
   eBay's word, the way the server works it out, so a list read an hour ago
   still says "ended" for a sale whose end has passed. */
export function effectiveState(event, now = Date.now()) {
  if (!event) return "ended";
  const status = String(event.status || "").toUpperCase();
  if (status === "ENDED" || status === "INVALID") return "ended";
  const end = time(event.end_at);
  if (end != null && end <= now) return "ended";
  if (status === "DRAFT") return "draft";
  if (status === "PAUSED") return "paused";
  const start = time(event.start_at);
  if (start != null && start > now) return "scheduled";
  return "running";
}

/* The Sales screen's three groups, each in the order a seller reads it:
   running by soonest end, scheduled by soonest start, ended newest first. */
export function groupSaleEvents(events, now = Date.now()) {
  const groups = { running: [], scheduled: [], ended: [] };
  for (const e of events || []) {
    const s = effectiveState(e, now);
    if (s === "ended") groups.ended.push(e);
    else if (s === "scheduled" || s === "draft") groups.scheduled.push(e);
    else groups.running.push(e);
  }
  groups.running.sort((a, b) => (time(a.end_at) ?? Infinity) - (time(b.end_at) ?? Infinity));
  groups.scheduled.sort((a, b) => (time(a.start_at) ?? Infinity) - (time(b.start_at) ?? Infinity));
  groups.ended.sort((a, b) => (time(b.end_at) ?? 0) - (time(a.end_at) ?? 0));
  return groups;
}

/* Sales still to come or running — the ones a listing can be IN. */
export const openSaleEvents = (events, now = Date.now()) =>
  (events || []).filter((e) => effectiveState(e, now) !== "ended");

/* listing record id -> the sale it is in, through each listing's eBay item
   id. A Map, so a card asks it in O(1) on every render of a big grid. */
export function saleIndex(events, items, now = Date.now()) {
  const byItem = new Map();
  for (const e of openSaleEvents(events, now)) {
    for (const id of e.item_ids || []) {
      if (!byItem.has(String(id))) byItem.set(String(id), e);
    }
  }
  const out = new Map();
  if (!byItem.size) return out;
  for (const item of items || []) {
    const id = String(item?.listing?.ebay_listing_id || "");
    if (id && byItem.has(id)) out.set(item.id, byItem.get(id));
  }
  return out;
}

/* What a buyer pays during the sale, to the cent. eBay rounds its own
   displayed figure (VERIFY how), so the card says "about" in its tooltip. */
export function salePriceFor(price, percent) {
  const p = Number(price), pct = Number(percent);
  if (!(p > 0) || !(pct > 0)) return null;
  return Math.round(p * (1 - pct / 100) * 100) / 100;
}

const DAY = 86400000;

/* "ends in 3d" / "ends today" / "starts Dec 1" — the short clause a card
   and a sale card both use. */
export function saleWhen(event, now = Date.now()) {
  const state = effectiveState(event, now);
  if (state === "scheduled" || state === "draft") {
    const start = time(event.start_at);
    return start ? `starts ${shortDate(start)}` : "not started";
  }
  if (state === "ended") {
    const end = time(event.end_at);
    return end ? `ended ${shortDate(end)}` : "ended";
  }
  const end = time(event.end_at);
  if (end == null) return "running";
  const left = end - now;
  if (left < DAY) return left < 3600000 ? "ends within the hour" : "ends today";
  const days = Math.ceil(left / DAY);
  return `ends in ${days}d`;
}

export function shortDate(t) {
  const d = new Date(t);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/* "Nov 24 – Dec 1". */
export function saleRange(event) {
  const a = time(event?.start_at), b = time(event?.end_at);
  if (a == null && b == null) return "";
  if (a == null) return `until ${shortDate(b)}`;
  if (b == null) return `from ${shortDate(a)}`;
  return `${shortDate(a)} – ${shortDate(b)}`;
}

/* How far through a running sale is, 0..1 — the bar on its card. */
export function saleProgress(event, now = Date.now()) {
  const a = time(event?.start_at), b = time(event?.end_at);
  if (a == null || b == null || b <= a) return 0;
  return Math.min(1, Math.max(0, (now - a) / (b - a)));
}

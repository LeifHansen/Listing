import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import { apiUrl } from "@/lib/platform";

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

// Guard against double-submits: without this a double-click or Enter-repeat
// fires the request twice (two AI charges, two drafts, or racing publishes).
const _inFlight = {};
export function once(key, fn) {
  return async (...args) => {
    if (_inFlight[key]) return;
    _inFlight[key] = true;
    try {
      return await fn(...args);
    } finally {
      _inFlight[key] = false;
    }
  };
}

export function mediaUrl(sessionId, name, bust) {
  // bust === true → always refetch; any other truthy value → stable version
  // key (cache holds until the value changes, e.g. a listing's updated_at).
  const v = bust === true ? `?v=${Date.now()}`
    : bust ? `?v=${encodeURIComponent(bust)}` : "";
  return apiUrl(`/media/${sessionId}/optimized/${name}${v}`);
}

export function formatMoney(n, currency = "USD") {
  if (n == null || n === "" || Number.isNaN(Number(n))) return null;
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency", currency, maximumFractionDigits: 2,
    }).format(Number(n));
  } catch (e) {
    return `$${Number(n).toFixed(2)}`;
  }
}

// "2h ago" / "3d ago" — coarse on purpose; the exact time is in the tooltip.
// Shared by the notifications bell, the messages panel and the message thread;
// a third copy of this is where drift starts.
export function timeAgo(iso) {
  if (!iso) return "";
  const mins = Math.max(0, (Date.now() - Date.parse(iso)) / 60000);
  if (!Number.isFinite(mins)) return "";
  if (mins < 1) return "just now";
  if (mins < 60) return `${Math.floor(mins)}m ago`;
  if (mins < 60 * 24) return `${Math.floor(mins / 60)}h ago`;
  return `${Math.floor(mins / 1440)}d ago`;
}

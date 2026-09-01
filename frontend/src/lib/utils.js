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

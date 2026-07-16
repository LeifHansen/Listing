import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

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

export const CONDITIONS = [
  "NEW", "NEW_OTHER", "NEW_WITH_DEFECTS", "CERTIFIED_REFURBISHED",
  "SELLER_REFURBISHED", "USED_EXCELLENT", "USED_VERY_GOOD", "USED_GOOD",
  "USED_ACCEPTABLE", "FOR_PARTS_OR_NOT_WORKING",
];

export function conditionLabel(c) {
  return String(c || "").replaceAll("_", " ").toLowerCase()
    .replace(/\b\w/g, (m) => m.toUpperCase());
}

export function mediaUrl(sessionId, name) {
  return `/media/${sessionId}/optimized/${name}`;
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

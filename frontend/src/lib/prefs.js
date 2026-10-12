/* The seller's account defaults (`/api/prefs`), read once per asker.
 *
 * Settings owns the editing of these and keeps its own copy; this is for the
 * screens that only READ one -- the search preview wants to know whether
 * offers are on, so it can say what the listing will say. Single-flight,
 * like loadPolicies in ShippingPolicySelect: several askers on one page send
 * one request. Not cached past that, on purpose: Settings can change a
 * default at any time, and a cache would show the editor last week's answer. */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

let inFlight = null;
export function loadPrefs() {
  if (!inFlight) {
    inFlight = api("/api/prefs")
      .then((r) => r?.prefs || {})
      .finally(() => { inFlight = null; });
  }
  return inFlight;
}

/** The prefs, or null until they arrive (and null for good on a failure --
 *  a preview that cannot say whether offers are on says nothing about it). */
export function usePrefs() {
  const [prefs, setPrefs] = useState(null);
  useEffect(() => {
    let alive = true;
    loadPrefs().then((p) => { if (alive) setPrefs(p); }).catch(() => {});
    return () => { alive = false; };
  }, []);
  return prefs;
}

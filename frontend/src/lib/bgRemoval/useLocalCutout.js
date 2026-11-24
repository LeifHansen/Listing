/* What this device can do about backgrounds, for any screen that asks:
   {status, provider, reason, progress}, where status is "off" (the server
   has not switched an engine on for this platform), "unknown"/"checking",
   "unsupported", "needs-download", "downloading" or "ready".

   Kept OUT of the app store on purpose: it is per device rather than per
   account, it is configured by the store (configureLocal, from
   /api/bg-removal/status) but owned here, and the many tests that stand in
   for the store with a handful of fields stay exactly as they are. */
import { useSyncExternalStore } from "react";
import { getSnapshot, localUsable, subscribe } from "./service";

export function useLocalCutout() {
  const snap = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return { ...snap, usable: localUsable(snap), ready: snap.status === "ready" };
}

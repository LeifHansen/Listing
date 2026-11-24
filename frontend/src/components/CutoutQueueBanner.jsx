import { useSyncExternalStore } from "react";
import { Loader2, Scissors, X } from "lucide-react";
import { useApp } from "@/store";

const NOTHING = () => () => {};
const IDLE = { running: false, total: 0, done: 0, failed: 0 };

// "Taking backgrounds off on this device" -- the device queue's progress
// (lib/bgRemoval/queue), shown beside the bulk banner while it runs. The
// photos it has not reached are safe as shot, so the seller can keep
// working, stop it, or close the app: what is left waits for next time.
export function CutoutQueueBanner() {
  const { cutoutQueue } = useApp();
  const state = useSyncExternalStore(
    cutoutQueue?.subscribe || NOTHING,
    () => cutoutQueue?.getState?.() || IDLE,
    () => IDLE,
  );
  if (!cutoutQueue || !state.running || !state.total) return null;
  const at = Math.min(state.done + state.failed + 1, state.total);
  return (
    <div className="mb-4 w-full flex items-center gap-3 rounded-card border p-4 text-sm text-ink bg-blue-soft border-blue/30"
      role="status" data-testid="cutout-queue-banner">
      <Loader2 size={17} className="text-blue shrink-0 animate-spin" aria-hidden />
      <span className="flex-1 min-w-0">
        <strong className="font-semibold">
          <Scissors size={14} className="inline -mt-0.5 mr-1" aria-hidden />
          Removing backgrounds on this device — {at} of {state.total}.
        </strong>{" "}
        Keep working; each photo updates as it’s done.
      </span>
      <button type="button" onClick={() => cutoutQueue.stop()}
        className="shrink-0 inline-flex items-center gap-1 font-semibold text-blue hover:underline cursor-pointer"
        aria-label="Stop removing backgrounds on this device">
        <X size={15} aria-hidden /> Stop
      </button>
    </div>
  );
}

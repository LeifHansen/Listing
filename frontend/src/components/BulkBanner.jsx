import { CheckCircle2, Loader2, X } from "lucide-react";
import { cn } from "@/lib/utils";

// "A bulk batch is processing" — the tap-to-return banner shown while a batch
// runs in the background. Two places need it and they differ only in where
// the tap goes: the app shell sends you to the Sell tab, the Sell screen
// itself (where the queue was replaced by the lists) swaps the queue back in.
// It lived as two copies that had to be kept in sync by hand.
//
// `done` is the finished batch. The banner used to key off "a batch exists"
// alone, so a batch that had already finished kept claiming to be processing —
// spinner and all — on every screen until the seller reopened the queue and
// exited it. A finished batch says so, drops the spinner, and can be dismissed
// on the spot.
export function BulkBanner({ done = false, onReview, onDismiss }) {
  return (
    <div className={cn(
      "mb-4 w-full flex items-center gap-3 rounded-card border p-4 text-sm text-ink",
      done ? "bg-success-soft border-success/30" : "bg-blue-soft border-blue/30",
    )}>
      <button
        type="button"
        onClick={onReview}
        className="flex-1 min-w-0 flex items-center gap-3 text-left cursor-pointer"
      >
        {done
          ? <CheckCircle2 size={17} className="text-success shrink-0" aria-hidden />
          : <Loader2 size={17} className="text-blue shrink-0 animate-spin" aria-hidden />}
        <span className="flex-1 min-w-0">
          {done ? (
            <>
              <strong className="font-semibold">Your bulk batch finished.</strong>{" "}
              Every item is saved to Drafts — tap to review the results.
            </>
          ) : (
            <>
              <strong className="font-semibold">A bulk batch is processing.</strong>{" "}
              Finished items save to Drafts automatically — tap to watch or review it.
            </>
          )}
        </span>
        <span className={cn("font-semibold shrink-0", done ? "text-success" : "text-blue")}>
          Review →
        </span>
      </button>
      {done && onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          title="Dismiss"
          className="shrink-0 grid place-items-center size-7 rounded-full text-ink-secondary
                     hover:text-ink hover:bg-black/5 cursor-pointer transition-colors"
        >
          <X size={15} aria-hidden />
        </button>
      )}
    </div>
  );
}

import { Loader2 } from "lucide-react";

// "A bulk batch is processing" — the tap-to-return banner shown while a batch
// runs in the background. Two places need it and they differ only in where
// the tap goes: the app shell sends you to the Sell tab, the Sell screen
// itself (where the queue was replaced by the lists) swaps the queue back in.
// It lived as two copies that had to be kept in sync by hand.
export function BulkBanner({ onReview }) {
  return (
    <button
      type="button"
      onClick={onReview}
      className="mb-4 w-full flex items-center gap-3 rounded-card bg-blue-soft border border-blue/30 p-4 text-left text-sm text-ink hover:border-blue/50 transition-colors cursor-pointer"
    >
      <Loader2 size={17} className="text-blue shrink-0 animate-spin" aria-hidden />
      <span className="flex-1 min-w-0">
        <strong className="font-semibold">A bulk batch is processing.</strong>{" "}
        Finished items save to Drafts automatically — tap to watch or review it.
      </span>
      <span className="font-semibold text-blue shrink-0">Review →</span>
    </button>
  );
}

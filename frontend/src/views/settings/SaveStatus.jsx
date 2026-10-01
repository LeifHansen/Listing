import { CheckCircle2, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * The inline answer beside a control that saves itself: Saving… / Saved /
 * Couldn’t save. Rendered as a live region so a screen reader hears the
 * answer where a sighted seller sees the tick.
 */
export function SaveStatus({ status, onRetry, className }) {
  const kind = status?.kind || "idle";
  return (
    <span
      role="status"
      aria-live="polite"
      className={cn("inline-flex items-center gap-1.5 text-xs min-h-4", className)}
    >
      {kind === "saving" && (
        <span className="inline-flex items-center gap-1 text-ink-faint">
          <Loader2 size={13} className="animate-spin" aria-hidden /> Saving…
        </span>
      )}
      {kind === "saved" && (
        <span className="inline-flex items-center gap-1 text-green font-semibold">
          <CheckCircle2 size={13} aria-hidden /> Saved
        </span>
      )}
      {kind === "error" && (
        <span className="inline-flex items-center gap-2 text-error">
          <span>Couldn’t save — {status.message}</span>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="font-semibold underline underline-offset-2 bg-transparent border-0 p-0 cursor-pointer text-error"
            >
              Try again
            </button>
          )}
        </span>
      )}
    </span>
  );
}

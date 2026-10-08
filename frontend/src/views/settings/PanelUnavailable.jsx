import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/Button";

// A panel whose data could not be loaded. Deliberately NOT the same thing as
// a panel with nothing in it: this screen exists to tell the seller what is
// saved, so rendering the app's fallbacks after a failed read would have it
// state, confidently, something it does not know.
export function PanelUnavailable({ message, onRetry }) {
  return (
    <div className="rounded-tile bg-warning-soft border border-warning/30 p-4 text-sm max-w-lg">
      <p className="text-ink flex gap-2">
        <AlertTriangle size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
        <span>{message}</span>
      </p>
      {onRetry && (
        <Button size="sm" variant="soft" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

// The one sentence every defaults panel shows after a failed read. Verbatim
// in one place because two tests and the deploy smoke test pin it.
export const DEFAULTS_UNAVAILABLE =
  "We couldn’t load your saved defaults just now, so nothing is shown here — "
  + "this isn’t what you have saved. Try again in a moment.";

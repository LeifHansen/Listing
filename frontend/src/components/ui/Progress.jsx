import { cn } from "@/lib/utils";

// BrandProgress — the chunky, logo-colored progress bar for long-running jobs
// (bulk batches). The fill flows through the logo palette — coral → gold →
// teal → cornflower — with a shine on its leading edge, and the percentage
// rides the bar itself. Animation lives in tokens.css (.brand-progress-fill).
export function BrandProgress({ value, caption, className }) {
  const pct = Math.max(0, Math.min(100, Math.round(value ?? 0)));
  // The fill IS the number, drawn: at 0 the track is empty. It used to floor
  // itself at 6% so the bar would "read alive" from the first paint, and the
  // seller read that as progress that had not happened — a label saying 0%
  // over a fill that plainly was not. Once there is progress the fill is never
  // narrower than its own height (`min-w-6` on an `h-6` track), so 1% is a
  // round bead at the start of the track rather than a squashed sliver.
  // The % label sits inside the fill once there's room; before that it rides
  // just past the fill's leading edge, in ink on the track — anchored to the
  // fill itself so it follows the real edge, bead and all.
  const labelInside = pct >= 16;
  return (
    <div className={className}>
      <div
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        className="relative h-6 rounded-full bg-bg-sunken border border-line-strong overflow-hidden"
      >
        <div
          className={cn(
            "brand-progress-fill absolute inset-y-0 left-0 rounded-full transition-[width] duration-700 ease-out",
            pct > 0 && "min-w-6",
          )}
          style={{ width: `${pct}%` }}
        >
          {labelInside ? (
            <span className="absolute inset-y-0 right-2.5 inline-flex items-center font-display text-xs font-bold tabular-nums text-white [text-shadow:0_1px_2px_rgb(23_40_74/0.55)]">
              {pct}%
            </span>
          ) : (
            <span className="absolute inset-y-0 left-full inline-flex items-center pl-2.5 font-display text-xs font-bold tabular-nums text-ink whitespace-nowrap">
              {pct}%
            </span>
          )}
        </div>
      </div>
      {caption && (
        <p className={cn("mt-2 text-[13px] text-ink-secondary tabular-nums text-center")}>
          {caption}
        </p>
      )}
    </div>
  );
}

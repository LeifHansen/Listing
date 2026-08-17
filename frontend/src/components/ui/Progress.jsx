import { cn } from "@/lib/utils";

// BrandProgress — the chunky, logo-colored progress bar for long-running jobs
// (bulk batches). The fill flows through the logo palette — coral → gold →
// teal → cornflower — with a shine on its leading edge, and the percentage
// rides the bar itself. Animation lives in tokens.css (.brand-progress-fill).
export function BrandProgress({ value, caption, className }) {
  const pct = Math.max(0, Math.min(100, Math.round(value ?? 0)));
  // Never a bare sliver — the bar reads "alive" from the first paint.
  const fillPct = Math.max(pct, 6);
  // The % label sits inside the fill once there's room; before that it chases
  // the leading edge from the outside, in ink on the track.
  const labelInside = fillPct >= 16;
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
          className="brand-progress-fill absolute inset-y-0 left-0 rounded-full transition-[width] duration-700 ease-out"
          style={{ width: `${fillPct}%` }}
        >
          {labelInside && (
            <span className="absolute inset-y-0 right-2.5 inline-flex items-center text-xs font-extrabold tabular-nums text-white [text-shadow:0_1px_2px_rgb(23_40_74/0.55)]">
              {pct}%
            </span>
          )}
        </div>
        {!labelInside && (
          <span
            className="absolute inset-y-0 inline-flex items-center pl-2.5 text-xs font-extrabold tabular-nums text-ink transition-[left] duration-700 ease-out"
            style={{ left: `${fillPct}%` }}
          >
            {pct}%
          </span>
        )}
      </div>
      {caption && (
        <p className={cn("mt-2 text-[13px] text-ink-secondary tabular-nums text-center")}>
          {caption}
        </p>
      )}
    </div>
  );
}

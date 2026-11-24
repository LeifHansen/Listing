import { useId } from "react";
import { cn } from "@/lib/utils";
import { DISCOUNT_CHOICES, DURATION_CHOICES } from "@/lib/saleEvents";

/* The two questions every sale asks — how much off, for how long — as chips
 * with a custom box, shared by the bulk bar's "Apply discount…" and the Sales
 * screen's New sale dialog so the two never offer different numbers. */

function Chip({ on, onClick, children, disabled }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={on} disabled={disabled}
      className={cn(
        "shrink-0 h-8 px-3 rounded-full text-[13px] font-semibold border tabular-nums",
        "cursor-pointer transition-colors duration-150 disabled:opacity-45 disabled:cursor-default",
        on ? "bg-blue text-on-accent border-blue"
          : "bg-card text-ink-secondary border-line hover:text-ink hover:border-line-strong",
      )}>
      {children}
    </button>
  );
}

/* `value` is a number or "" while the custom box is being typed in. */
export function PercentField({ value, onChange, limits, disabled, label = "How much off" }) {
  const id = useId();
  const choices = DISCOUNT_CHOICES.filter((p) => p >= limits.min_percent && p <= limits.max_percent);
  const custom = value !== "" && !choices.includes(Number(value));
  return (
    <div className="flex flex-col gap-1.5" role="group" aria-labelledby={id}>
      <span id={id} className="text-[13px] font-semibold text-ink">{label}</span>
      <div className="flex flex-wrap items-center gap-1.5">
        {choices.map((p) => (
          <Chip key={p} on={Number(value) === p && !custom} disabled={disabled}
            onClick={() => onChange(p)}>{p}%</Chip>
        ))}
        <label className={cn("inline-flex items-center gap-1 h-8 pl-3 pr-2 rounded-full border text-[13px] font-semibold",
          custom ? "border-blue text-ink" : "border-line text-ink-secondary")}>
          Other
          <input type="number" inputMode="numeric" min={limits.min_percent} max={limits.max_percent}
            step={1} value={custom ? value : ""} disabled={disabled}
            onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
            aria-label="Other percentage"
            className="w-12 bg-transparent text-right tabular-nums focus:outline-none" />
          %
        </label>
      </div>
    </div>
  );
}

/* `value` is a number of days, or "until" with `until` holding a date
   (yyyy-mm-dd). */
export function DurationField({ value, onChange, until, onUntil, limits, disabled }) {
  const id = useId();
  const choices = DURATION_CHOICES.filter((d) => d <= limits.max_days);
  return (
    <div className="flex flex-col gap-1.5" role="group" aria-labelledby={id}>
      <span id={id} className="text-[13px] font-semibold text-ink">Runs for</span>
      <div className="flex flex-wrap items-center gap-1.5">
        {choices.map((d) => (
          <Chip key={d} on={value === d} disabled={disabled} onClick={() => onChange(d)}>
            {d} days
          </Chip>
        ))}
        {onUntil && (
          <Chip on={value === "until"} disabled={disabled} onClick={() => onChange("until")}>
            Until a date
          </Chip>
        )}
        {value === "until" && onUntil && (
          <input type="date" value={until || ""} onChange={(e) => onUntil(e.target.value)}
            aria-label="Last day of the sale" disabled={disabled}
            className="h-8 rounded-full border border-line bg-card px-3 text-[13px]" />
        )}
      </div>
    </div>
  );
}

/* Is this a percentage the server will take? */
export const percentOk = (v, limits) => {
  const n = Number(v);
  return v !== "" && Number.isInteger(n) && n >= limits.min_percent && n <= limits.max_percent;
};

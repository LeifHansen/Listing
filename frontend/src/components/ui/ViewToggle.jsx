import { LayoutGrid, Rows3 } from "lucide-react";
import { cn } from "@/lib/utils";

/* ViewToggle — grid (default) or list, for the listing grids on the Sell
   screen. Two icon buttons in one pill: a radio group in behaviour, so the
   pressed one reads as the current layout rather than as a toggle you have
   armed. `aria-pressed` (not a checkbox) keeps it announceable without
   inventing a label for a control that's icon-only by design. */

const OPTIONS = [
  { id: "grid", icon: LayoutGrid, label: "Grid view" },
  { id: "list", icon: Rows3, label: "List view" },
];

export function ViewToggle({ value = "grid", onChange, className }) {
  return (
    <div
      role="group"
      aria-label="Listing layout"
      className={cn(
        "inline-flex items-center gap-0.5 rounded-full border border-line bg-card p-0.5 shrink-0",
        className,
      )}
    >
      {OPTIONS.map(({ id, icon: Icon, label }) => {
        const active = value === id;
        return (
          <button
            key={id}
            type="button"
            onClick={() => onChange?.(id)}
            aria-pressed={active}
            aria-label={label}
            title={label}
            className={cn(
              "grid place-items-center size-8 rounded-full cursor-pointer",
              "transition-colors duration-150",
              active
                ? "bg-blue text-on-accent"
                : "text-ink-faint hover:text-ink hover:bg-bg-sunken",
            )}
          >
            <Icon size={16} aria-hidden />
          </button>
        );
      })}
    </div>
  );
}

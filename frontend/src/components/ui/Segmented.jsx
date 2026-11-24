import { cn } from "@/lib/utils";

/* A row of mutually exclusive choices in one pill — "All · eBay · Etsy", or
 * "Listings · Sales". A neutral pressed state on purpose: blue is the
 * lifecycle tabs', and a second blue row made two kinds of tab look like one.
 *
 * `options`: [{ id, label, count, title }]. `count` (optional) is a small
 * number after the label. */
export function Segmented({ options, value, onChange, label, size = "sm", className }) {
  return (
    <div role="group" aria-label={label}
      className={cn("inline-flex items-center gap-0.5 rounded-full border border-line bg-card p-0.5 shrink-0",
        className)}>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          onClick={() => onChange(o.id)}
          aria-pressed={value === o.id}
          title={o.title}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full font-semibold cursor-pointer",
            "transition-colors duration-150",
            size === "md" ? "h-9 px-4 text-[13.5px]" : "h-8 px-3 text-[12px]",
            value === o.id ? "bg-bg-sunken text-ink" : "text-ink-secondary hover:text-ink",
          )}
        >
          {o.label}
          {o.count != null && o.count > 0 && (
            <span className="font-display tabular-nums text-[11px] font-bold rounded-full bg-blue text-on-accent px-1.5 min-w-5 h-5 grid place-items-center">
              {o.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

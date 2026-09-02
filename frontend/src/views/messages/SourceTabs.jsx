import { cn } from "@/lib/utils";

// The marketplace toggle. One inbox, many marketplaces — this is how a seller
// narrows it to one, and how the app stays honest about the ones it can't do
// yet ("Depop · soon") instead of pretending they aren't marketplaces at all.
//
// Hidden entirely when only one source can actually deliver messages: a filter
// with a single option is furniture.
export function SourceTabs({ sources, value, onChange, unreadBySource }) {
  const live = (sources || []).filter((s) => s.available);
  const pending = (sources || []).filter((s) => !s.available && s.supported);
  if (live.length < 2) return null;

  const tab = (key, label, count, disabled, title) => (
    <button
      key={key || "all"}
      type="button"
      disabled={disabled}
      title={title}
      onClick={() => onChange(key)}
      aria-pressed={value === key}
      className={cn(
        "inline-flex items-center gap-1.5 h-8 px-3 rounded-full text-[12px] font-semibold",
        "transition-colors duration-150",
        disabled
          ? "text-ink-faint cursor-not-allowed"
          : value === key
            ? "bg-blue text-on-accent cursor-pointer"
            : "text-ink-secondary hover:bg-bg-sunken cursor-pointer",
      )}
    >
      {label}
      {count > 0 && (
        <span className={cn(
          "grid place-items-center min-w-[16px] h-4 px-1 rounded-full text-[10px] font-bold tabular-nums",
          value === key ? "bg-white/25 text-on-accent" : "bg-blue text-on-accent",
        )}>
          {count > 9 ? "9+" : count}
        </span>
      )}
    </button>
  );

  return (
    <div className="flex items-center gap-1 flex-wrap" role="group"
      aria-label="Filter by marketplace">
      {tab("", "All", 0, false)}
      {live.map((s) => tab(s.key, s.label, unreadBySource?.[s.key] || 0, false))}
      {pending.map((s) => tab(s.key, `${s.label} · soon`, 0, true,
        s.message || "Not connected for messages yet"))}
    </div>
  );
}

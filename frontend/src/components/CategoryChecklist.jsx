import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/fields";
import { cn } from "@/lib/utils";

/* A ticked list of categories with how many listings sit in each — the body
 * of the Manage tab's "Store category" and "eBay category" pickers, used both
 * in the desktop popovers and in the Filters panel, so the two can never
 * offer different choices.
 *
 * `options`: [{ id, name, path, count, level, orphan }] (lib/categoryOptions).
 * `selected`: the ids ticked. A search box appears once the list is long
 * enough to need one; `limit` shows the most-used first with a "Show all".
 */
export function CategoryChecklist({
  options, selected, onToggle, onClear, label, limit = 0, emptyText,
}) {
  const [q, setQ] = useState("");
  const [all, setAll] = useState(false);
  const picked = useMemo(() => new Set(selected || []), [selected]);
  const needle = q.trim().toLowerCase();
  const matching = needle
    ? options.filter((o) => `${o.name} ${o.path}`.toLowerCase().includes(needle))
    : options;
  // A ticked one is never hidden behind "Show all": what is narrowing the
  // grid has to be visible where it is undone.
  const capped = !needle && !all && limit > 0 && matching.length > limit;
  const shown = capped
    ? matching.filter((o, i) => i < limit || picked.has(o.id))
    : matching;

  if (!options.length) {
    return <p className="text-[13px] text-ink-secondary px-1 py-1.5">{emptyText || "Nothing to pick yet."}</p>;
  }

  return (
    <div className="flex flex-col gap-1.5 min-w-0" role="group" aria-label={label}>
      {options.length > 10 && (
        <div className="relative">
          <Search size={14} aria-hidden
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint pointer-events-none" />
          <Input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder={`Search ${label.toLowerCase()}`} aria-label={`Search ${label.toLowerCase()}`}
            className="h-9 text-sm pl-8" />
        </div>
      )}
      <ul className="max-h-64 overflow-y-auto -mx-1 px-1 flex flex-col">
        {shown.map((o) => (
          <li key={o.id}>
            <label
              title={o.path || o.name}
              className={cn(
                "flex items-center gap-2.5 rounded-tile px-2 py-1.5 cursor-pointer select-none",
                "text-[13.5px] hover:bg-bg-sunken",
                picked.has(o.id) ? "text-ink font-semibold" : "text-ink-secondary",
              )}
              style={o.level > 1 ? { paddingLeft: `${0.5 + (o.level - 1) * 0.9}rem` } : undefined}
            >
              <input type="checkbox" checked={picked.has(o.id)} onChange={() => onToggle(o.id)}
                className="size-4 shrink-0 accent-(--brand-blue) cursor-pointer" />
              <span className={cn("flex-1 min-w-0 truncate", o.orphan && "italic")}>{o.name}</span>
              <span className="shrink-0 tabular-nums text-[12px] text-ink-faint">{o.count}</span>
            </label>
          </li>
        ))}
        {needle && !matching.length && (
          <li className="px-2 py-1.5 text-[13px] text-ink-faint">No category matches “{q.trim()}”.</li>
        )}
      </ul>
      {(capped || (picked.size > 0 && onClear)) && (
        <div className="flex items-center justify-between gap-2 px-1">
          {capped ? (
            <button type="button" onClick={() => setAll(true)}
              className="text-[12.5px] font-semibold text-blue hover:underline cursor-pointer">
              Show all ({matching.length})
            </button>
          ) : <span />}
          {picked.size > 0 && onClear && (
            <button type="button" onClick={onClear}
              className="text-[12.5px] text-ink-faint hover:text-ink underline underline-offset-2 cursor-pointer">
              Clear
            </button>
          )}
        </div>
      )}
    </div>
  );
}

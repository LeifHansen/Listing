import { AnimatePresence } from "framer-motion";
import { MoreHorizontal, X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Menu } from "@/components/ui/Menu";
import { cn } from "@/lib/utils";

/* The Manage tab's selection bar: arrives with the first tick on a live
 * listing and leaves with the last.
 *
 * Two rows, so the actions stay in order on a phone instead of staggering
 * down the right edge: what is ticked (and the way out) above, the actions
 * below. With eight things a tick can lead to, they cannot all be buttons —
 * Shopify's admin and the crosslisting tools all land on the same answer: the
 * few a seller reaches for every week are on the bar, and the rest sit under
 * "More". One primary (End & relist), the rest bordered.
 *
 * `actions`: [{ key, label, icon, onClick, primary, loading, disabled, title,
 *   place }], where `place` is
 *     "bar"       — on the bar at every size;
 *     "bar-wide"  — on the bar from small screens up, under More on a phone;
 *     "more"      — always under More.
 * The phone keeps a two-column grid of what is left plus More, and More
 * spans the row when it would otherwise sit alone in half of it.
 *
 * `panel` is whatever the open action needs before it can run (an amount, a
 * shelf), drawn under the bar so the ticks stay in view while it is chosen. */
export function LiveBulkBar({
  count, total, allSelected, filtered, onToggleAll, onClear, running,
  actions, panel, busy,
}) {
  const shown = actions.filter(Boolean);
  const onBar = shown.filter((a) => a.place === "bar" || a.place === "bar-wide");
  const phoneBar = shown.filter((a) => a.place === "bar");
  const menuItems = [
    ...shown.filter((a) => a.place === "bar-wide").map((a) => ({ ...a, className: "sm:hidden" })),
    ...shown.filter((a) => a.place === "more"),
  ].map((a) => ({
    key: a.key, label: a.label, icon: a.icon, onSelect: a.onClick,
    disabled: a.disabled, danger: a.danger, divider: a.divider,
    title: a.title, className: a.className,
  }));
  // More alone in the last half-row of the phone's grid spans it instead.
  const moreSpans = (phoneBar.length + 1) % 2 === 1;

  return (
    <div className="sticky top-2 z-20 flex flex-col gap-2 rounded-card
      border border-blue/35 bg-blue-soft/90 backdrop-blur px-3 py-2.5 shadow-card">
      <div className="flex items-center gap-2 min-w-0">
        <label className="flex items-center gap-2 text-[13px] font-semibold text-ink cursor-pointer select-none min-w-0">
          <input
            type="checkbox"
            checked={allSelected}
            ref={(el) => { if (el) el.indeterminate = count > 0 && !allSelected; }}
            onChange={onToggleAll}
            className="size-4 accent-(--brand-blue) cursor-pointer"
          />
          {/* "shown", while a filter is on: Select all reaches what is on
              screen and nothing the seller filtered out. */}
          {filtered ? `Select all ${total} shown` : "Select all"}
          <span className="text-ink-secondary font-medium tabular-nums">
            ({count} of {total})
          </span>
        </label>
        {running && (
          <span className="text-[13px] text-ink-secondary tabular-nums truncate min-w-0"
            role="status" aria-live="polite">
            {running.verb} {Math.min(running.done + 1, running.total)} of {running.total}
            {running.title ? ` · ${running.title}` : ""}
          </span>
        )}
        <Button variant="ghost" size="sm" className="ml-auto shrink-0" onClick={onClear}>
          <X aria-hidden /> Clear
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:justify-end">
        {onBar.map((a) => (
          <Button key={a.key}
            variant={a.primary ? "primary" : "secondary"} size="sm"
            className={cn("w-full sm:w-auto", a.place === "bar-wide" && "hidden sm:inline-flex")}
            onClick={a.onClick} loading={a.loading} disabled={a.disabled}
            title={a.title} aria-expanded={a.expanded}>
            {a.icon} {a.barLabel || a.label}
          </Button>
        ))}
        {menuItems.length > 0 && (
          <Menu label="More bulk actions" size="sm" variant="secondary"
            items={menuItems} disabled={busy}
            className={cn("w-full sm:w-auto", moreSpans && "col-span-2 sm:col-span-1")}
            triggerClassName="w-full sm:w-auto">
            <MoreHorizontal aria-hidden /> More
          </Menu>
        )}
      </div>
      <AnimatePresence initial={false}>{panel}</AnimatePresence>
    </div>
  );
}

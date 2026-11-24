import { useState } from "react";
import { motion } from "framer-motion";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Toggle } from "@/components/ui/fields";
import { cn } from "@/lib/utils";
import { effectiveState, saleWhen } from "@/lib/saleEvents";

/* "Add to sale…": the seller's sales they can still add to (running or still
 * to come, and made the way the app can edit), one tap to pick, plus "New
 * sale…" for when none of them is it. */
export function AddToSalePanel({ count, sales, inOtherSales = 0, busy, onSubmit, onNew, onCancel }) {
  const open = sales.filter((e) => e.editable && effectiveState(e) !== "ended");
  const [picked, setPicked] = useState(open[0]?.id || "");
  const [move, setMove] = useState(false);
  const chosen = open.find((e) => e.id === picked);
  return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      className="overflow-hidden"
    >
      <div className="rounded-[13px] border border-line bg-bg-sunken p-3.5 flex flex-col gap-3">
        {open.length ? (
          <div role="radiogroup" aria-label="Which sale" className="flex flex-col gap-1.5">
            {open.map((e) => (
              <label key={e.id} className={cn(
                "flex items-center gap-2.5 rounded-tile border px-3 py-2 cursor-pointer bg-card",
                picked === e.id ? "border-blue ring-1 ring-blue/40" : "border-line hover:border-line-strong",
              )}>
                <input type="radio" name="sale" checked={picked === e.id}
                  onChange={() => setPicked(e.id)} className="accent-(--brand-blue)" />
                <span className="flex-1 min-w-0">
                  <span className="block text-[13.5px] font-semibold text-ink truncate">{e.name}</span>
                  <span className="block text-[12px] text-ink-secondary">
                    {e.percent_off}% off · {saleWhen(e)} · {e.count ?? 0} listing{e.count === 1 ? "" : "s"}
                  </span>
                </span>
              </label>
            ))}
          </div>
        ) : (
          <p className="text-[13px] text-ink-secondary">
            No sale to add to yet — start one for these {count}.
          </p>
        )}
        {inOtherSales > 0 && open.length > 0 && (
          <Toggle checked={move} onChange={setMove}
            label={`Move the ${inOtherSales} already in another sale`}
            note="A listing can only be in one sale. Off, those are left where they are." />
        )}
        <div className="flex flex-wrap items-center gap-2">
          {open.length > 0 && (
            <Button variant="primary" size="sm" loading={busy} disabled={busy || !chosen}
              onClick={() => onSubmit({ event: chosen, move })}>
              Add {count} to “{chosen?.name || "sale"}”
            </Button>
          )}
          <Button variant={open.length ? "secondary" : "primary"} size="sm" disabled={busy} onClick={onNew}>
            <Plus aria-hidden /> New sale…
          </Button>
          <Button variant="ghost" size="sm" disabled={busy} onClick={onCancel}>Cancel</Button>
        </div>
      </div>
    </motion.div>
  );
}

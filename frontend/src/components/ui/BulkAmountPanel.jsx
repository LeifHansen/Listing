import { useState } from "react";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

/* "Lower every price by [ 10 ]%" — the amount a bulk action needs before it
 * can run, with its own submit. Rendered in normal flow under the control
 * that opened it rather than as a floating panel: the cards these sit in
 * clip overflow, so anything absolutely positioned gets cut off.
 *
 * `amount` describes the number: { unit, initial, min, max, step, label,
 * submit(count, value, total), note }. `count` is how many listings this
 * run reaches and `total` how many were asked for; when they differ the
 * panel says so next to the button that spends it, rather than only in the
 * toast that arrives once it is too late to plan. */
export function BulkAmountPanel({ amount, count, total = count, busy, onSubmit, onCancel,
                                  className }) {
  const { unit, initial, min, max, step, label, submit, note } = amount;
  const [value, setValue] = useState(initial);
  const valid = Number(value) >= min && Number(value) <= max;
  const apply = () => { if (valid) onSubmit(Number(value)); };
  return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      className="overflow-hidden"
    >
      <div className={cn("rounded-[13px] border border-line bg-bg-sunken p-3.5", className)}>
        <label className="flex flex-wrap items-center gap-2 text-[13px] font-semibold text-ink">
          {label}
          <span className="inline-flex items-center gap-1">
            <input
              type="number" inputMode="decimal"
              min={min} max={max} step={step} value={value}
              autoFocus
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") apply();
                if (e.key === "Escape") onCancel();
              }}
              className={cn(
                "w-20 rounded-lg border bg-bg px-2 py-1 text-sm font-bold tabular-nums text-ink",
                "focus:outline-none focus:ring-2 focus:ring-blue/40",
                valid ? "border-line" : "border-error",
              )}
            />
            <span className="text-ink-secondary">{unit}</span>
          </span>
        </label>
        {note && <p className="mt-2 text-[12px] text-ink-secondary">{note}</p>}
        {count < total && (
          <p className="mt-1 text-[12px] text-ink-secondary">
            One run covers {count} of them — the other {total - count}{" "}
            {total - count === 1 ? "stays" : "stay"} on the list for a second run.
          </p>
        )}
        {!valid && (
          <p className="mt-1 text-[12px] font-semibold text-error">
            Enter a number between {min} and {max}.
          </p>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button variant="primary" size="sm" loading={busy}
            disabled={busy || !valid} onClick={apply}>
            {submit(count, value, total)}
          </Button>
          <Button variant="ghost" size="sm" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </div>
    </motion.div>
  );
}

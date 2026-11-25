import { useState } from "react";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/Button";
import { Toggle } from "@/components/ui/fields";
import { DurationField, PercentField, percentOk } from "./DiscountFields";

/* "Apply discount…": a quick sale over the ticks — how much off, for how
 * long — that eBay runs and ends on its own, with the old price struck
 * through for every buyer. Under the bar, like the other bulk amounts, so the
 * ticks stay in view. The asking prices never move: this is not Lower prices.
 *
 * `inOtherSales` is how many of the ticks are already in a sale the app can
 * edit; only then is the "move them" switch offered. */
export function DiscountPanel({ count, inOtherSales = 0, limits, busy, onSubmit, onCancel }) {
  const [percent, setPercent] = useState(20);
  const [days, setDays] = useState(7);
  const [move, setMove] = useState(false);
  const [opened] = useState(() => Date.now());
  const ok = percentOk(percent, limits);
  const end = new Date(opened + days * 86400000);
  const endLabel = end.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      className="overflow-hidden"
    >
      <div className="rounded-[13px] border border-line bg-bg-sunken p-3.5 flex flex-col gap-3">
        <PercentField value={percent} onChange={setPercent} limits={limits} disabled={busy} />
        <DurationField value={days} onChange={setDays} limits={limits} disabled={busy} />
        <p className="text-[12px] text-ink-secondary">
          Buyers see your price struck through until {endLabel}, then it ends by
          itself. Your asking prices don’t change.
        </p>
        {inOtherSales > 0 && (
          <Toggle checked={move} onChange={setMove}
            label={`Move the ${inOtherSales} already in another sale`}
            note="A listing can only be in one sale. Off, those are left where they are." />
        )}
        {!ok && percent !== "" && (
          <p className="text-[12px] font-semibold text-error">
            Pick a whole number from {limits.min_percent} to {limits.max_percent}%.
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" size="sm" loading={busy} disabled={busy || !ok}
            onClick={() => onSubmit({ percent: Number(percent), days, move })}>
            {ok ? `Put ${count} on sale at ${percent}% off` : `Put ${count} on sale`}
          </Button>
          <Button variant="ghost" size="sm" disabled={busy} onClick={onCancel}>Cancel</Button>
        </div>
      </div>
    </motion.div>
  );
}

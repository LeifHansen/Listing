import { useState } from "react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/fields";
import { DurationField, PercentField, percentOk } from "@/components/bulk/DiscountFields";
import { effectiveState } from "@/lib/saleEvents";
import { cn } from "@/lib/utils";

const DAY = 86400000;
const dateInput = (t) => {
  const d = new Date(t);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};
// A yyyy-mm-dd from a date box, as the START of that day here, or its END.
const dayStart = (s) => (s ? new Date(`${s}T00:00:00`) : null);
const dayEnd = (s) => (s ? new Date(`${s}T23:59:00`) : null);
const short = (d) => d.toLocaleDateString(undefined, { month: "short", day: "numeric" });

/* New sale, or edit one. A sale is four answers: what it's called, how much
 * off, when it starts, how long it runs — and, for a new one, which listings.
 *
 * `ticked` is the live listings ticked on Manage; `shelves` the seller's
 * store categories; `activeCount` how many live listings the store has.
 * `onSubmit(body)` sends it and resolves when the server has answered. */
export function SaleDialog({
  open, onClose, event = null, ticked = [], shelves = [], activeCount = 0,
  limits, onSubmit,
}) {
  // The moment the dialog opened: every date below is measured from it, so
  // the summary does not drift while the seller is choosing.
  const [now] = useState(() => Date.now());
  const editing = !!event;
  const started = editing && effectiveState(event, now) === "running";
  const [name, setName] = useState(event?.name || "");
  const [percent, setPercent] = useState(event?.percent_off ?? 20);
  const [startMode, setStartMode] = useState("now");
  const [startDay, setStartDay] = useState(() => dateInput(now + DAY));
  const [duration, setDuration] = useState(editing ? "until" : 7);
  const [until, setUntil] = useState(() => (editing && event.end_at
    ? dateInput(Date.parse(event.end_at)) : dateInput(now + 7 * DAY)));
  const [scope, setScope] = useState(ticked.length ? "ticked" : shelves.length ? "shelf" : "all");
  const [shelf, setShelf] = useState(shelves[0] ? String(shelves[0].id) : "");
  const [sending, setSending] = useState(false);

  const begin = editing
    ? new Date(event.start_at ? Date.parse(event.start_at) : now)
    : startMode === "now" ? new Date(now) : dayStart(startDay);
  const finish = duration === "until" ? dayEnd(until)
    : new Date(begin.getTime() + Number(duration) * DAY);
  const days = finish && begin ? (finish - begin) / DAY : 0;
  const howMany = scope === "ticked" ? ticked.length
    : scope === "shelf" ? null : activeCount;

  const problems = [];
  if (!percentOk(percent, limits)) {
    problems.push(`Pick a whole number from ${limits.min_percent} to ${limits.max_percent}% off.`);
  }
  if (!finish || finish.getTime() <= now || finish <= begin) {
    problems.push("The sale has to end after it starts.");
  } else if (days > limits.max_days + 0.01) {
    problems.push(`eBay runs a sale for at most ${limits.max_days} days.`);
  }
  if (!editing && scope === "ticked" && !ticked.length) problems.push("Tick some live listings first.");
  if (!editing && scope === "shelf" && !shelf) problems.push("Pick a store category.");

  const submit = async () => {
    if (problems.length) return;
    setSending(true);
    const body = editing ? {
      ...(name.trim() && name.trim() !== event.name ? { name: name.trim() } : {}),
      ...(!started && Number(percent) !== event.percent_off ? { percent: Number(percent) } : {}),
      end_at: finish.toISOString(),
    } : {
      name: name.trim(),
      percent: Number(percent),
      ...(startMode === "date" ? { start_at: begin.toISOString() } : {}),
      ...(duration === "until" ? { end_at: finish.toISOString() } : { days: Number(duration) }),
      ...(scope === "ticked" ? { listing_ids: ticked.map((i) => i.id) }
        : scope === "shelf" ? { store_category_id: shelf } : { all_active: true }),
    };
    try {
      const ok = await onSubmit(body);
      if (ok !== false) onClose();
    } finally {
      setSending(false);
    }
  };

  const placeholder = `${percentOk(percent, limits) ? `${percent}% off` : "Sale"}`
    + `${duration !== "until" ? ` · ${duration} days` : ""}`;

  return (
    <Dialog open={open} onClose={sending ? () => {} : onClose}
      title={editing ? "Edit sale" : "New sale"}>
      <div className="flex flex-col gap-4">
        <Field label="Name" hint="(only you see this)">
          <Input value={name} maxLength={limits.name_max} placeholder={placeholder}
            onChange={(e) => setName(e.target.value)} className="h-10 text-sm" />
        </Field>

        <PercentField value={percent} onChange={setPercent} limits={limits} disabled={started}
          label={started ? "How much off (fixed once a sale starts)" : "How much off"} />

        {!editing && (
          <div className="flex flex-col gap-1.5" role="group" aria-label="Starts">
            <span className="text-[13px] font-semibold text-ink">Starts</span>
            <div className="flex flex-wrap items-center gap-1.5">
              {[["now", "Now"], ["date", "On a date"]].map(([id, label]) => (
                <button key={id} type="button" aria-pressed={startMode === id}
                  onClick={() => setStartMode(id)}
                  className={cn("h-8 px-3 rounded-full text-[13px] font-semibold border cursor-pointer",
                    startMode === id ? "bg-blue text-on-accent border-blue"
                      : "bg-card text-ink-secondary border-line hover:text-ink")}>
                  {label}
                </button>
              ))}
              {startMode === "date" && (
                <input type="date" value={startDay} min={dateInput(now)}
                  onChange={(e) => setStartDay(e.target.value)} aria-label="First day of the sale"
                  className="h-8 rounded-full border border-line bg-card px-3 text-[13px]" />
              )}
            </div>
          </div>
        )}

        <DurationField value={duration} onChange={setDuration} until={until} onUntil={setUntil}
          limits={limits} />

        {!editing && (
          <div className="flex flex-col gap-1.5" role="radiogroup" aria-label="Which listings">
            <span className="text-[13px] font-semibold text-ink">Which listings</span>
            {[
              ["ticked", `The ${ticked.length} you ticked on Listings`, ticked.length > 0],
              ["shelf", "Everything in a store category", shelves.length > 0],
              ["all", `All active listings (${activeCount})`, activeCount > 0],
            ].filter(([, , show]) => show).map(([id, label]) => (
              <label key={id} className="flex items-center gap-2 text-[13.5px] text-ink cursor-pointer">
                <input type="radio" name="scope" checked={scope === id} onChange={() => setScope(id)}
                  className="accent-(--brand-blue)" />
                {label}
              </label>
            ))}
            {scope === "shelf" && (
              <Select aria-label="Store category" value={shelf} onChange={(e) => setShelf(e.target.value)}
                className="h-9 text-sm max-w-xs">
                {shelves.map((c) => <option key={c.id} value={String(c.id)}>{c.name}</option>)}
              </Select>
            )}
            {scope !== "ticked" && (
              <p className="text-[12px] text-ink-secondary">
                The listings there right now — ones you list later aren’t added.
              </p>
            )}
          </div>
        )}

        <p className="rounded-tile bg-bg-sunken px-3 py-2 text-[13px] text-ink">
          {percentOk(percent, limits) ? `${percent}% off` : "A discount"}
          {editing ? "" : howMany != null ? ` on ${howMany} listing${howMany === 1 ? "" : "s"}` : ""}
          {finish && begin && finish > begin ? ` · ${short(begin)} – ${short(finish)}` : ""}
          {" · buyers see your price struck through, and it ends by itself."}
        </p>

        {problems.length > 0 && (
          <ul className="text-[12.5px] font-semibold text-error list-disc pl-5">
            {problems.map((p) => <li key={p}>{p}</li>)}
          </ul>
        )}

        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={sending}>Cancel</Button>
          <Button variant="primary" onClick={submit} loading={sending}
            disabled={sending || problems.length > 0}>
            {editing ? "Save changes" : startMode === "date" ? "Schedule sale" : "Start sale"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

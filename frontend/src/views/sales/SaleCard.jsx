import { CalendarClock, ExternalLink, Lock, Pencil, CircleStop, ListFilter } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import { effectiveState, saleProgress, saleRange, saleWhen } from "@/lib/saleEvents";

const STATE_STYLE = {
  running: "bg-success-soft text-success",
  scheduled: "bg-blue-soft text-blue",
  draft: "bg-bg-sunken text-ink-secondary",
  paused: "bg-warning-soft text-warning",
  ended: "bg-bg-sunken text-ink-faint",
};
const STATE_LABEL = {
  running: "Running", scheduled: "Scheduled", draft: "Draft in Seller Hub",
  paused: "Paused", ended: "Ended",
};

/* One sale: how much off, when, how many listings, and where it was made.
 * A sale this app can't edit says so on the card, with the reason and a
 * way to Seller Hub, rather than offering buttons that would be refused. */
export function SaleCard({ event, onViewListings, onEdit, onEnd, busy }) {
  const state = effectiveState(event);
  const live = state === "running";
  const progress = live ? saleProgress(event) : 0;
  const pct = event.percent_off;
  return (
    <article className={cn(
      "rounded-card border border-line bg-card p-4 flex flex-col gap-3 shadow-card",
      state === "ended" && "opacity-75",
    )}>
      <header className="flex items-start gap-3">
        <div className="shrink-0 grid place-items-center rounded-tile bg-blue-soft text-blue
          font-display font-bold tabular-nums size-14 text-[19px] leading-none">
          {pct != null ? `${Math.round(pct)}%` : "—"}
          <span className="text-[10px] font-semibold -mt-2">{pct != null ? "off" : "varies"}</span>
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-[15px] text-ink truncate" title={event.name}>
            {event.name || "Untitled sale"}
          </h3>
          <p className="text-[12.5px] text-ink-secondary flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-bold", STATE_STYLE[state])}>
              {STATE_LABEL[state]}
            </span>
            <span className="inline-flex items-center gap-1">
              <CalendarClock size={12} aria-hidden /> {saleRange(event)}
            </span>
            {state !== "ended" && <span>· {saleWhen(event)}</span>}
          </p>
        </div>
      </header>

      {live && (
        <div className="h-1.5 rounded-full bg-bg-sunken overflow-hidden" aria-hidden>
          <div className="h-full bg-success rounded-full" style={{ width: `${Math.round(progress * 100)}%` }} />
        </div>
      )}

      <p className="text-[12.5px] text-ink-secondary flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-semibold text-ink tabular-nums">
          {event.count == null ? "Listings picked by rule"
            : `${event.count} listing${event.count === 1 ? "" : "s"}`}
        </span>
        <span>· {event.source === "app" ? "Made here" : "From Seller Hub"}</span>
        {!event.editable && event.read_only_reason && state !== "ended" && (
          <span className="inline-flex items-center gap-1 rounded-full bg-bg-sunken px-2 py-0.5 text-[11px] font-semibold text-ink-secondary"
            title="This app keeps sales simple — one percentage off a list of listings. This one is set up another way, so it's shown here and changed in Seller Hub.">
            <Lock size={11} aria-hidden /> Managed in Seller Hub · {event.read_only_reason}
          </span>
        )}
      </p>

      <div className="flex flex-wrap items-center gap-2">
        {event.count > 0 && state !== "ended" && (
          <Button variant="soft" size="sm" onClick={() => onViewListings(event)}>
            <ListFilter aria-hidden /> View listings
          </Button>
        )}
        {event.editable && (
          <Button variant="ghost" size="sm" onClick={() => onEdit(event)} disabled={busy}>
            <Pencil aria-hidden /> Edit
          </Button>
        )}
        {event.editable && (
          <Button variant="ghost" size="sm" onClick={() => onEnd(event)} disabled={busy}
            className="text-error hover:text-error">
            <CircleStop aria-hidden /> {state === "running" ? "End sale" : "Cancel sale"}
          </Button>
        )}
        {!event.editable && state !== "ended" && (
          <a href={event.seller_hub_url} target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1 text-[13px] font-semibold text-blue hover:underline">
            Open in Seller Hub <ExternalLink size={13} aria-hidden />
          </a>
        )}
      </div>
    </article>
  );
}

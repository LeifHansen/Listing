/* Crosspost to Etsy — the listings a seller ticked in the manager, read the
   way Etsy will read them.

   The first step is a plain answer to "what does Etsy still need on these
   twelve?": one row per listing, with either the reason it is left out
   (already on Etsy, an auction, variations, not live) or the fields Etsy
   would refuse it over, judged by the same rules the Publish button uses
   (etsyBlockers) against the shop's own defaults. Nothing is sent from
   here yet; "Open" takes the seller to the listing to answer them. */
import { useMemo } from "react";
import { CheckCircle2, ExternalLink } from "lucide-react";
import { useApp } from "@/store";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import { candidates, tally } from "@/lib/crosspost";

export function CrosspostWizard({ items, onClose }) {
  const { etsyOptions, openListing } = useApp();
  const etsySettings = etsyOptions && !etsyOptions.error ? etsyOptions : null;
  const rows = useMemo(() => candidates(items, { etsySettings }), [items, etsySettings]);
  const t = tally(rows);

  return (
    <Dialog open onClose={onClose} title="Crosspost to Etsy" wide>
      <p className="text-sm text-ink-secondary">
        What Etsy still needs from each listing — its own category, who made it
        and when, and the shop's shipping, return and processing profiles. A
        listing already on Etsy, an auction, or one with variations is left out.
      </p>
      <ul className="mt-4 flex flex-col divide-y divide-line" aria-label="Listings to crosspost">
        {rows.map(({ item, skip, blockers, ready }) => (
          <li key={item.id} className="py-3 flex flex-wrap items-start gap-3">
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-ink truncate">
                {item.listing?.title || item.title || "Untitled"}
              </p>
              {skip ? (
                <p className="text-[13px] text-ink-faint mt-0.5" data-skip>{skip}</p>
              ) : ready ? (
                <p className="text-[13px] text-success mt-0.5 flex items-center gap-1" data-ready>
                  <CheckCircle2 size={14} aria-hidden /> Ready for Etsy
                </p>
              ) : (
                <span className="flex flex-wrap items-center gap-1.5 mt-1" data-needs>
                  {blockers.map((b) => (
                    <span key={b.key} title={b.why}
                      className="inline-flex items-center rounded-full bg-warning-soft border border-warning/40 px-2 py-0.5 text-[12px] font-bold text-warning">
                      {b.label}
                    </span>
                  ))}
                </span>
              )}
            </div>
            {!skip && (
              <Button variant="soft" size="sm" onClick={() => { onClose(); openListing(item.id); }}>
                <ExternalLink aria-hidden /> Open
              </Button>
            )}
          </li>
        ))}
      </ul>
      <div className={cn("mt-4 flex flex-wrap items-center justify-between gap-3",
        "border-t border-line pt-4")}>
        <p className="text-[13px] text-ink-secondary tabular-nums" data-tally>
          {t.ready} ready · {t.needing} need something · {t.skipped} skipped
        </p>
        <Button variant="secondary" onClick={onClose}>Close</Button>
      </div>
    </Dialog>
  );
}

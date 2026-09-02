import { Sparkles, AlertTriangle, CheckCircle2, ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Button } from "@/components/ui/Button";
import { WorkflowCard } from "./WorkflowCard";

/* Finish up — the last step of the listing workflow before Publish.

   One button that reads the listing's own photos and fills in everything
   eBay still asks for: the category if it is missing, every item specific
   the photos can answer, and the maker. It only ever fills BLANKS — anything
   already written is left exactly as it is — so it is safe to press on a
   listing that is nearly done.

   Why it lives HERE and not only on the dashboard: the dashboard's "Enrich
   all" has to land the same fill on listings that are already live on eBay,
   and that path has four ways to come back with the blanks still blank — no
   category eBay agrees with, photos that live on eBay rather than on the
   server, no connected account, or a ReviseItem eBay won't take. A draft has
   none of those. Filling a listing in before it is published is the same
   work with nothing in the way, and it is also the point at which the seller
   is looking at the listing and can see what changed. */

// What the card says is left, when something is. The blocker list is already
// the app's single answer to "why won't this publish" (see blockers.js), so
// this names the same fields rather than inventing a second opinion.
function StillNeeded({ blockers, isLive, onJump }) {
  const n = blockers.length;
  const many = n === 1 ? "1 field" : `${n} fields`;
  return (
    <div className="rounded-tile bg-warning-soft border border-warning/30 p-4">
      <p className="font-bold text-sm text-ink flex items-center gap-2">
        <AlertTriangle size={17} className="text-warning" aria-hidden />
        {/* A listing that is already live plainly IS on eBay — what these
            fields hold up is the update, not the listing. Same distinction
            the publish bar draws. */}
        {isLive
          ? `${many} eBay won't accept`
          : `${many} ${n === 1 ? "is" : "are"} keeping this off eBay`}
      </p>
      <p className="text-[13px] text-ink-secondary mt-1">
        Fill in details answers what the photos can. Anything still listed
        after that is yours — tap one to jump to it.
      </p>
      <div className="flex flex-wrap items-center gap-1.5 mt-3">
        {blockers.map((b) => (
          <button
            key={b.key}
            type="button"
            onClick={() => onJump(b.target)}
            title={b.why}
            className={cn(
              "inline-flex items-center gap-1 rounded-full bg-card border border-warning/40",
              "px-2.5 py-0.5 text-[12px] font-bold text-warning cursor-pointer",
              "hover:border-warning transition-colors",
            )}
          >
            {b.label} <ArrowRight size={11} aria-hidden />
          </button>
        ))}
      </div>
    </div>
  );
}

export function FinishUpCard({ w }) {
  const { tokens } = useApp();
  const { confirm } = useToast();
  const blockers = w.blockers;
  const ready = blockers.length === 0;
  const isLive = w.isLive;

  // Every press spends AI credits, so it says what it costs before it does
  // anything — the same promise the dashboard's bulk version makes.
  const cost = tokens.enabled && tokens.costs?.specifics
    ? ` It uses ${tokens.costs.specifics} AI token${tokens.costs.specifics === 1 ? "" : "s"}; you have ${tokens.total}.`
    : "";

  const run = async () => {
    if (!(await confirm({
      title: "Fill in the details from your photos?",
      message: "The AI reads this listing's own photos and fills in what eBay "
        + "asks for — the item specifics buyers filter by, the maker, and the "
        + `category if it's still blank. Anything you've written is left exactly as it is.${cost}`,
      confirmLabel: "Fill them in",
    }))) return;
    await w.fillInDetails();
  };

  const jump = (target) => {
    if (!target) return;
    w.setFixTarget(null);
    // Re-set next frame so an already-flagged card re-triggers its scroll.
    requestAnimationFrame(() => w.setFixTarget(target));
  };

  return (
    <WorkflowCard
      id="finish" icon={Sparkles} title="Finish up"
      hint="Let the AI fill in everything eBay still asks for — the last step before publishing"
      state={ready ? "complete" : "todo"}
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-ink-secondary">
          One pass over this listing's photos fills in the item specifics eBay
          wants for its category, double-checks the maker, and picks the
          category itself if it's still blank. It only fills what's empty —
          nothing you've written is touched.
        </p>
        {/* On a live listing the fill is saved here and marked as changed, so
            it goes out with the next revise. Saying so is the difference
            between "it worked" and "why is eBay still blank?". */}
        {isLive && (
          <p className="text-[13px] text-ink-secondary">
            This listing is live — what's filled in here reaches eBay when you
            press <strong className="text-ink">Update Live Listing</strong>.
          </p>
        )}
        {ready ? (
          <div className="rounded-tile bg-success-soft border border-success/25 p-4 flex gap-3">
            <CheckCircle2 size={18} className="text-success shrink-0 mt-0.5" aria-hidden />
            <p className="text-sm text-ink">
              <span className="font-semibold">Nothing is blocking this listing.</span>{" "}
              Filling in the details is still worth a press — the specifics
              eBay doesn't demand are the ones buyers filter by.
            </p>
          </div>
        ) : (
          <StillNeeded blockers={blockers} isLive={isLive} onJump={jump} />
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="primary" size="lg" onClick={run} disabled={!!w.aiBusy}>
            <Sparkles aria-hidden /> Fill in details
          </Button>
          {!!cost && (
            <span className="text-[13px] text-ink-faint">{cost.trim()}</span>
          )}
        </div>
      </div>
    </WorkflowCard>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, ArrowRight, CheckCircle2, CircleStop, Combine, PenLine,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api, postJson } from "@/lib/api";
import { apiUrl } from "@/lib/platform";
import { useApp } from "@/store";
import { isDraft } from "@/lib/listingsView";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { AIStatusCard } from "@/components/ui/AIStatus";
import { BrandProgress } from "@/components/ui/Progress";
import { useToast } from "@/components/ui/Toaster";
import { usePublishTargets } from "./publishShared";
import { ebayBlockers } from "./blockers";
import { duplicateSuspects } from "./duplicateSuspects";
import { DraftsStrip } from "./DraftsStrip";

/* Bulk mode: one photo dump spanning many items. The server groups the photos,
   identifies each item, and saves each one as a draft as it finishes; this
   component polls the job, reports what the batch is doing, and hands the
   review over to the drafts grid.

   It used to draw its own grid of item cards: its own tile with the title and
   price as text boxes, its own columns, its own Publish/Delete/Merge
   buttons, its own selection. Two grids of the same drafts, and a seller who
   opened one item from a batch and saved it was handed back the OTHER one —
   different cards, different layout, different controls, for the same five
   listings. So the review below is DraftsStrip, scoped to the batch's own
   ids: one grid of draft cards in the app, and the trip through the editor
   comes back to exactly the screen it left.

   What stays here is what belongs to the batch rather than to a draft: the
   progress of the job, the stop switch, and what the run is worth saying
   afterwards — duplicates to look at, how many can't reach eBay yet, what
   went live, and the items the AI could not identify at all (those have no
   draft for the grid to show). */

const PHASE_MESSAGES = {
  uploading: ["Uploading your photo pile…"],
  optimizing: ["Optimizing photos…", "Straightening sideways shots…"],
  grouping: ["Sorting photos into items…", "Matching angles of the same item…"],
  identifying: ["Identifying items…", "Writing titles & prices…", "Detecting brands…"],
};

// Background removal is a per-upload choice, so the progress text has to be
// one too: this said "removing backgrounds" over every batch, including the
// ones that never asked for it. job.remove_bg is what the batch was actually
// started with (absent on batches started before this shipped — which reads
// as "off", the safe way round: it never claims work that isn't happening).
function phaseMessages(phase, removeBg) {
  if (phase === "optimizing" && removeBg) {
    return ["Optimizing photos…", "Straightening & removing backgrounds…"];
  }
  return PHASE_MESSAGES[phase] || ["Working…"];
}

/* An item the batch could not turn into a draft.
 *
 * There is no listing behind it: nothing to publish, nothing to open, and no
 * row in the store for the drafts grid to show — so it needs a card of its
 * own. Same columns as that grid, so a batch still reads as one screen: what
 * the AI drafted, and what it could not.
 */
function LostItemCard({ item, onDismiss, dismissing }) {
  const thumb = item.thumb ? apiUrl(`${item.thumb}?v=1`) : null;
  return (
    <Card className="py-4 border-warning/50 bg-warning-soft">
      <div className="flex items-start gap-3">
        {thumb && (
          <img
            src={thumb}
            alt=""
            loading="lazy"
            className="size-12 shrink-0 rounded-[10px] object-cover border border-line"
            onError={(e) => { e.currentTarget.style.display = "none"; }}
          />
        )}
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 text-[13px] font-bold text-ink">
            <AlertTriangle size={14} className="shrink-0 text-warning" aria-hidden />
            <span className="min-w-0 truncate">
              {item.title || "Couldn't identify this item"}
            </span>
          </p>
          <p className="mt-1 text-[13px] text-ink-secondary">
            {item.error || "Couldn't identify this item."}
          </p>
        </div>
        {/* The way off the screen for something that produced nothing. Not a
            draft, so this is a dismissal: it takes the row with it where one
            exists, and simply clears the card where it never did. */}
        <Button variant="ghost" size="sm" onClick={onDismiss} loading={dismissing}
          aria-label="Dismiss this item" title="Take this off the batch"
          className="shrink-0 text-ink-faint hover:text-error">
          <Trash2 aria-hidden />
        </Button>
      </div>
    </Card>
  );
}


// How long this screen keeps polling a batch it cannot reach before it gives
// up watching. Long enough to ride out a deploy: the app runs on ONE machine,
// a deploy replaces it, and the new one answers about a minute later with the
// batch picked back up where it stopped (main._resume_interrupted_batches).
// Six polls at three seconds gave up inside that window every time, and the
// seller was told the connection was lost while the batch went on without
// them.
const RESTART_GRACE_MS = 4 * 60 * 1000;

export function BulkQueue({ jobId, onExit, onSettled }) {
  const { openListing, loadListings, listingsState } = useApp();
  const { toast } = useToast();

  // Which marketplaces a publish from this screen would go to — read only to
  // judge what eBay would refuse in the "can't reach eBay yet" count below.
  // The chips that CHANGE it live in the drafts grid's header, where the
  // publish buttons are (see publishShared, DraftsStrip).
  const { effectiveTargets } = usePublishTargets();
  const [job, setJob] = useState(null);
  const [items, setItems] = useState([]);
  // Items the AI could not identify: which are being dismissed, and which
  // the seller has dismissed.
  const [dismissing, setDismissing] = useState({});
  const [dismissed, setDismissed] = useState({});
  // Watching was given up on (job gone, or too many failed polls). Without it
  // the pre-first-poll "Uploading…" state below would spin forever.
  const [unwatched, setUnwatched] = useState(false);
  // "Stop batch" has been sent and accepted. Latched so the button can't be
  // hit twice in the up-to-1.5s before the next poll brings back the finished
  // job — and so it can say "Stopping…" instead of looking ignored.
  const [stopping, setStopping] = useState(false);
  const stopped = useRef(false);
  const fails = useRef(0);
  // When the polls started failing. A deploy restarts the only server and
  // takes it away for a minute or two while the new one warms up; a batch
  // that was running is picked straight back up by the next boot, so the
  // watch has to outlast that window rather than a fixed count of polls.
  const failingSince = useRef(0);
  const notFound = useRef(0);
  // How many items the batch had drafted the last time the store was
  // refreshed. The grid below renders the SAVED drafts, so a newly drafted
  // item only appears once the listings are re-read — and re-reading them on
  // every poll would be a request every 1.5 seconds for a batch that is
  // still working on the same photo.
  const refreshedAt = useRef(0);

  // Poll the job until done; items render as they arrive. Resilient to transient
  // poll failures — a busy server (heavy batch) can blip a request even though
  // the job is still running, so we retry instead of abandoning the batch.
  useEffect(() => {
    // No job id yet — the batch is still uploading from the store, and the
    // screen shows the upload phase until the id lands.
    if (!jobId) return;
    stopped.current = false;
    fails.current = 0;
    failingSince.current = 0;
    notFound.current = 0;
    refreshedAt.current = 0;
    // Resets "we stopped watching" for the NEW job. It cannot cascade — the
    // effect keys on jobId and this writes neither jobId nor anything jobId
    // is derived from — and it has to happen here rather than during render,
    // because what it is synchronizing with is the poll loop started below,
    // not anything React can see.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setUnwatched(false);
    let timer;
    const poll = async () => {
      try {
        const j = await api(`/api/bulk/status/${jobId}`);
        if (stopped.current) return;
        fails.current = 0;
        failingSince.current = 0;
        notFound.current = 0;
        setJob(j);
        // The batch's own list of what it drafted: the ids the grid below is
        // scoped to, how many items the run produced, and the ones it could
        // not identify. The listing BODIES are no longer read from here —
        // the cards read the saved drafts, so an edit made in the editor (or
        // a category picked on another screen) is on the card rather than
        // behind the copy this job happened to hand back.
        if (j.items?.length) setItems(j.items);
        // A newly drafted item is a new saved draft, so the store has to be
        // re-read for its card to appear. Only when the count has actually
        // moved — and not on a batch that is already finished, which the
        // branch below refreshes once for the whole run.
        if (!j.done && (j.items || []).length > refreshedAt.current) {
          refreshedAt.current = (j.items || []).length;
          loadListings({ quiet: true });
        }
        if (!j.done) {
          timer = setTimeout(poll, 1500);
        } else {
          stopped.current = true;  // nothing left to watch — don't re-poll on focus
          loadListings({ quiet: true });
          // Stop persisting (a reload shouldn't restore a done batch), and say
          // which sessions it drafted so the shell's banner can retire itself
          // once they are all listed.
          onSettled?.((j.items || []).map((it) => it.session_id));
        }
      } catch (e) {
        if (stopped.current) return;
        fails.current += 1;
        if (!failingSince.current) failingSince.current = Date.now();
        // A 404 means the server has no record of this job at all. Confirm it
        // with a second poll before believing it: a status check can 404 on a
        // blip (an auth hiccup mid-batch does it) while the batch itself is
        // still running, and declaring it dead is not something we can undo.
        const gone = (e.message || "").includes("(404)");
        notFound.current = gone ? notFound.current + 1 : 0;
        if (gone && notFound.current < 2) {
          timer = setTimeout(poll, 3000);
        } else if (gone) {
          // Terminal. Stop polling for good — including the visibility/focus
          // handler below, which otherwise re-ran this on every tab switch —
          // and mark the batch finished so the queue shows what happened.
          // Without this the progress bar sat spinning on the photo the batch
          // died at, with no result, no error, and no way out of the screen.
          stopped.current = true;
          onSettled?.();
          setUnwatched(true);
          loadListings({ quiet: true });  // whatever it did finish is in Drafts
          // A finished job, so the queue renders the outcome card with this
          // reason and a way off the screen. Clearing `busy` alone (unwatched)
          // stops the spinner but leaves nothing behind it: no explanation,
          // and no exit at all for a batch that died before drafting anything.
          setJob((j) => ({
            ...(j || {}),
            done: true,
            error: "This batch stopped early — the server restarted while it "
              + "was working, and no record of it survived. Anything it "
              + "finished is saved in Drafts; the rest need another run.",
          }));
          toast("This batch was interrupted (the server restarted). Any items it finished are saved in Drafts.",
            { kind: "warning" });
        } else if (Date.now() - failingSince.current < RESTART_GRACE_MS) {
          // Transient, or the server is restarting under a deploy. Either
          // way the batch is most likely still running (or about to be
          // picked back up), so keep asking rather than walking away.
          timer = setTimeout(poll, 3000);
        } else {
          setUnwatched(true);
          // Give up watching but KEEP it persisted — the batch may still be
          // finishing server-side, so the banner lets the user reopen and resume.
          toast("Lost the connection while watching this batch — it may still be finishing. Reopen the Sell tab to check, and see Drafts for completed items.",
            { kind: "warning" });
        }
      }
    };
    poll();
    // Browsers throttle timers hard in a hidden tab (down to about once a
    // minute, and frozen entirely after a while), so a batch watched from a
    // background tab looks stuck until a manual refresh. Poll immediately
    // whenever the tab comes back to the foreground.
    const onVisible = () => {
      if (document.visibilityState !== "visible" || stopped.current) return;
      clearTimeout(timer);
      fails.current = 0;
      failingSince.current = 0;
      poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      stopped.current = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [jobId, loadListings, toast, onSettled]);

  // Stop the batch. Nothing is deleted and nothing is lost: every item the
  // batch has already drafted was saved as it finished and stays in Drafts, so
  // this doesn't stop to ask — a seller who wants out of a batch that isn't
  // moving should be out of it on the first tap. The server settles the job
  // immediately and the worker stands down at its next item, so the screen
  // lets go even when the batch itself is wedged somewhere it can't answer
  // from, which is the case this exists for.
  const stopBatch = useCallback(async () => {
    setStopping(true);
    try {
      await postJson(`/api/bulk/cancel/${jobId}`, {});
      toast("Stopping the batch — anything it already drafted is saved in Drafts.",
            { kind: "info" });
    } catch (e) {
      // Still running, so the button goes back rather than leaving the seller
      // looking at a "Stopping…" that never happened.
      setStopping(false);
      toast(`Couldn't stop the batch: ${e.message}`, { kind: "error" });
    }
  }, [jobId, toast]);

  // Take an item the AI could not identify off the batch.
  //
  // Best-effort server-side: a failed identify usually produced no record at
  // all, so a 404 is the expected answer here and clearing the card is the
  // whole job. (Drafts are deleted from their own card in the grid below,
  // with the confirm and the undo-less warning that deleting a real listing
  // deserves — this is not that.)
  const dismissOne = async (it) => {
    setDismissing((d) => ({ ...d, [it.session_id]: true }));
    try {
      await api(`/api/listings/${it.session_id}`, { method: "DELETE" });
    } catch (e) {
      if (!(e.message || "").includes("(404)")) {
        toast(`Couldn't dismiss that: ${e.message}`, { kind: "error" });
        setDismissing((d) => ({ ...d, [it.session_id]: false }));
        return;
      }
    }
    // Held in state rather than by filtering `items`: a batch that is still
    // running would otherwise bring the card back on its next poll.
    setDismissed((d) => ({ ...d, [it.session_id]: true }));
    setDismissing((d) => ({ ...d, [it.session_id]: false }));
    loadListings({ quiet: true });
  };

  // Busy from the very first frame: the batch screen goes up on the click, so
  // it opens on "Uploading your photo pile…" while the photos are still going
  // out — before there is a job id, let alone a status to poll.
  const busy = !unwatched && (!job || !job.done);
  const phase = job?.phase || "uploading";
  // Phase-weighted % for the progress bar shown while the job runs; the
  // cards stream in below it as each item is drafted.
  const pct = Math.round((() => {
    const frac = (cur, tot) => (tot ? Math.min(1, (cur || 0) / tot) : 0);
    // Nothing has happened before the server has the pile. A bar that opens
    // on 3% claims progress that was never made, and the seller noticed.
    if (!job) return 0;
    if (job.done) return 100;
    if (phase === "uploading") return 5;
    if (phase === "optimizing") return 10 + 35 * frac(job.current, job.total_photos);
    if (phase === "grouping") return 50;
    if (phase === "identifying") return 55 + 44 * frac(job.current, job.total_items);
    return 95;
  })());
  // The batch, as the store has it. `items` says WHICH sessions this run
  // touched and in what order; every fact about a listing — its title, its
  // price, whether it is still a draft — is read off the saved row, which is
  // what the grid below renders and what the seller's last save wrote. Two
  // sources for that used to disagree the moment anything was edited
  // anywhere else, and the batch screen was always the stale one.
  //
  // Memoized because it is handed to DraftsStrip as `only`, where it is a
  // dependency of that grid's own filter-and-sort memo. A fresh array here on
  // every render is a new identity there, which would miss that memo every
  // time and leave it re-filtering the whole store on each background poll —
  // the exact cost the memo was added to remove.
  const batchIds = useMemo(() => items.map((it) => it.session_id), [items]);
  const rows = new Map(
    (listingsState.items || []).map((it) => [String(it.id), it]));
  const batchRows = batchIds
    .map((id) => rows.get(String(id)))
    .filter(Boolean);
  const drafts = batchRows.filter(isDraft);
  // What went live and cleared out of the batch. Read from the store rather
  // than from a publish this screen happened to watch, so it still says so
  // after a reload — and so it counts the ones published from the grid below,
  // one card at a time, which is where publishing now happens.
  const published = batchRows.filter(
    (it) => it.status === "published" || it.status === "live");
  // Items the AI could not identify. These have no saved draft, so the grid
  // cannot show them and they get their own cards (see LostItemCard).
  const lost = items.filter(
    (it) => it.status === "error" && !dismissed[it.session_id]);
  // `it.listing || {}` like every other call site: a row can be sitting on no
  // listing at all (a draft that did not survive a restart), and this one
  // runs in the SCREEN's render, not a card's — so a throw here takes the
  // whole batch down rather than one card.
  const blocked = drafts.filter(
    (it) => ebayBlockers(it.listing || {}, { targets: effectiveTargets }).length > 0);
  // Memoized: this screen re-renders on every status poll and on every
  // listings refresh, and the pairwise scan is quadratic in the size of the
  // batch. Keyed on everything the scan reads — ids, titles, and the brand
  // and specifics it weighs against them — so a poll that changed nothing
  // re-uses the last answer, while a brand corrected on a card re-runs it.
  const dupeKey = drafts
    .map((d) => [
      d.id,
      d.listing?.title || d.title || "",
      d.listing?.brand || "",
      (d.listing?.item_specifics || [])
        .map((s) => `${s.name}=${s.value}`).join(","),
    ].join(":"))
    .join("|");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const dupes = useMemo(() => duplicateSuspects(drafts), [dupeKey]);
  const progressDetail = phase === "identifying" && job?.total_items
    ? ` (${job.current}/${job.total_items})`
    : phase === "optimizing" && job?.total_photos
      ? ` (${Math.min(job.current || 0, job.total_photos)}/${job.total_photos} photos)`
      : phase === "grouping" && job?.total_photos
        ? ` (${job.total_photos} photos)` : "";

  return (
    <div className="flex flex-col gap-4">
      {busy && (
        <div className="flex flex-col gap-3">
          <AIStatusCard messages={[
            // A batch the server picked back up after a restart keeps the same
            // job id, so this view just carries on polling. Say so, or the
            // count appearing to jump reads as a glitch.
            ...(job?.resumed ? ["Picking your batch back up where it stopped…"] : []),
            ...phaseMessages(phase, job?.remove_bg).map((m) => m + progressDetail),
          ]} />
          <BrandProgress
            className="px-1"
            value={pct}
            caption={items.length
              ? `${items.length} item${items.length === 1 ? "" : "s"} drafted so far`
              : null}
          />
          {/* Only once the server has the batch: before the job id lands there
              is nothing to stop yet, and the photos are still on their way. */}
          {jobId && (
            <div className="flex justify-end">
              <Button
                variant="ghost" size="sm" onClick={stopBatch} disabled={stopping}
                title="Stop this batch. Items already drafted stay in Drafts; the rest won't run."
              >
                <CircleStop aria-hidden /> {stopping ? "Stopping…" : "Stop batch"}
              </Button>
            </div>
          )}
        </div>
      )}
      {job?.done && (
        <Card className={cn("py-4", job.error ? "border-warning/40" : "border-success/30")}>
          <div className="flex flex-wrap items-center gap-3">
            {/* min-w rather than min-w-0: with two buttons beside it the
                verdict was squeezed to three wrapped words on a phone. It
                keeps a readable line and the buttons drop below it. */}
            <p className="text-sm font-semibold text-ink flex items-center gap-2 flex-1 min-w-[13rem]">
              {job.error
                ? <><AlertTriangle size={17} className="text-warning" aria-hidden /> {job.error}</>
                : job.cancelled
                ? <>
                    <CircleStop size={17} className="text-ink-secondary shrink-0" aria-hidden />
                    <span title="The rest of the batch never ran, so it wasn't charged for.">
                      You stopped this batch{items.length
                        ? ` — the ${items.length} item${items.length === 1 ? "" : "s"} it finished ${items.length === 1 ? "is" : "are"} saved in Drafts`
                        : " before it drafted anything"}.
                    </span>
                  </>
                : <>
                    <CheckCircle2 size={17} className="text-success" aria-hidden />
                    <span title="Also saved in Drafts — review below or come back anytime.">
                      {items.length} item{items.length === 1 ? "" : "s"} queued as drafts
                    </span>
                  </>}
            </p>
            <div className="flex flex-wrap items-center gap-2.5 ml-auto">
              {/* Off this screen, and on through the batch. "Start another
                  batch" is here rather than in a toolbar of its own, so a
                  batch that drafted nothing (a run that failed before it
                  identified anything) still has a way out. */}
              <Button variant="secondary" onClick={onExit}>
                <PenLine aria-hidden /> Start another batch
              </Button>
              {/* The guided path: step through each draft in the full editor
                  — preview, tweak, publish — and the post-publish screen's
                  "Next Draft" keeps the assembly line moving through the
                  batch. Opened by id, so the editor loads the draft as
                  SAVED: this used to hand over the batch job's own copy,
                  which was the one copy that never saw an edit made
                  anywhere else. */}
              {drafts.length > 0 && (
                <Button variant="primary" onClick={() => openListing(drafts[0].id)}
                  title="Review each draft in the full editor and publish as you go.">
                  Preview &amp; list <ArrowRight aria-hidden />
                </Button>
              )}
            </div>
          </div>
        </Card>
      )}

      {/* Background removal was asked for but couldn't run: the photos were
          deliberately kept unchanged, so say why instead of leaving the user
          to conclude the feature is broken. */}
      {job?.done && job.bg_error && (
        <Card className="py-3.5 border-warning/40 bg-warning-soft">
          <p className="text-sm text-ink flex items-start gap-2">
            <AlertTriangle size={17} className="text-warning shrink-0 mt-0.5" aria-hidden />
            <span title="The photos were saved unchanged.">
              <strong>Backgrounds weren't removed</strong> on {job.bg_failed || "some"}{" "}
              photo{job.bg_failed === 1 ? "" : "s"} —{" "}
              {String(job.bg_error).trim().replace(/[.!?]*$/, "")}. Your photos
              were saved unchanged.
            </span>
          </p>
        </Card>
      )}

      {job?.done && (() => {
        if (!dupes.length) return null;
        const [a, b] = dupes[0];
        return (
          <Card className="py-3.5 border-warning/40 bg-warning-soft">
            <p className="text-sm text-ink flex items-start gap-2">
              <Combine size={17} className="text-warning shrink-0 mt-0.5" aria-hidden />
              <span title="If they're the same item, tick one of them on its card and choose Merge into one — the dialog asks which draft it merges with before anything is written.">
                <strong>Possible duplicate{dupes.length > 1 ? "s" : ""}:</strong>{" "}
                "{(a.listing?.title || a.title || "").slice(0, 40)}…" &amp;{" "}
                "{(b.listing?.title || b.title || "").slice(0, 40)}…"
                {dupes.length > 1 ? ` (+${dupes.length - 1} more)` : ""} — tick
                one below, then <strong>Merge into one</strong>.
              </span>
            </p>
          </Card>
        );
      })()}

      {job?.done && blocked.length > 0 && (
        <Card className="py-3.5 border-warning/40 bg-warning-soft">
          <p className="text-sm text-ink flex items-start gap-2">
            <AlertTriangle size={17} className="text-warning shrink-0 mt-0.5" aria-hidden />
            {/* Each card names its OWN blocking fields — this banner only
                says how many are affected, so it can't contradict them. */}
            <span title="Each blocked card lists the fields eBay is refusing it over. Category, format and shipping are on the card itself; the rest are one tap away in Review & List.">
              <strong>{blocked.length}</strong> of {drafts.length} draft{drafts.length === 1 ? "" : "s"}{" "}
              can&apos;t reach eBay yet — each one is marked{" "}
              <strong className="text-warning">Blocked</strong> below, with the fields that are holding it.
            </span>
          </p>
        </Card>
      )}

      {/* The receipt. A card leaves the grid the moment its listing goes
          live, and it carried the eBay item id out with it — so the ids
          collect here instead. */}
      {published.length > 0 && (
        <Card className="py-3.5 border-success/30">
          <p className="text-sm text-ink flex items-start gap-2">
            <CheckCircle2 size={17} className="text-success shrink-0 mt-0.5" aria-hidden />
            <span title="Live on eBay — find them under Active in your listings.">
              <strong>{published.length} listing{published.length === 1 ? "" : "s"} published live</strong>
              {" "}and cleared out of this batch
              {published.some((it) => it.listing?.ebay_listing_id)
                ? ` — ${published.filter((it) => it.listing?.ebay_listing_id)
                    .map((it) => it.listing.ebay_listing_id).join(", ")}`
                : ""}.
              {drafts.length > 0
                ? " What's below still needs you."
                : ""}
            </span>
          </p>
        </Card>
      )}

      {/* The items the AI could not make a listing out of. Above the grid
          rather than mixed into it: they are the batch's own failures, not
          drafts, and nothing in the grid's vocabulary (Publish, Review &
          List, a price) applies to them. */}
      {lost.length > 0 && (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {lost.map((it) => (
            <LostItemCard
              key={it.session_id}
              item={it}
              onDismiss={() => dismissOne(it)}
              dismissing={!!dismissing[it.session_id]}
            />
          ))}
        </div>
      )}

      {/* The review itself: the drafts grid, scoped to this batch.
          The same component the Sell screen renders — the same cards, the
          same columns, the same grid/list toggle, the same Publish / Review &
          List / Delete, the same select-mode publish, merge and delete — so
          the seller works through a batch in the screen they already know,
          and coming back from the editor lands on the one they left.
          `publishAll` adds this screen's one-tap ending to its header. */}
      <DraftsStrip only={batchIds} publishAll />
    </div>
  );
}

import { useState } from "react";
import { motion } from "framer-motion";
import {
  Rocket, CheckCircle2, AlertTriangle, ArrowRight, Eye, ListChecks,
  RefreshCw, ExternalLink, Ban, Sparkles, X,
} from "lucide-react";
import { postJson } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { blockerHeadline, marketNames } from "./blockers";
import { MarketTargetChips, usePublishTargets } from "./publishShared";
import { Button } from "@/components/ui/Button";
import { endedGraceDays, keptWhenEnded } from "@/lib/listingsView";

// An issue is blocking unless it says otherwise. Preflight marks its advice
// with level "warn" (and, on newer servers, blocking:false); an eBay
// rejection carries neither, and every one of those stopped the publish.
const isBlocking = (it) => it.blocking !== false && it.level !== "warn";

// One issue: what's wrong, how to fix it, and a button that jumps to the
// field. `generic` issues have no field to jump to (an account-wide problem,
// an eBay outage) so they get no button — and neither does `account`, which
// is the same thing by another name: no section answers to it, so the button
// scrolled nowhere and left the seller pressing "Fix this" at a page that
// would not move.
function IssueRow({ it, onFix }) {
  return (
    <li className="text-sm">
      <p className="font-semibold text-ink">{it.title}</p>
      {it.fix && <p className="text-ink-secondary mt-0.5">{it.fix}</p>}
      {it.target && it.target !== "generic" && it.target !== "account" && (
        <Button variant="soft" size="sm" className="mt-2" onClick={() => onFix(it.target)}>
          Fix this <ArrowRight aria-hidden />
        </Button>
      )}
    </li>
  );
}

/* The fix-it list, in two groups that must never be mixed.

   The blocking group is the answer to "why isn't this on eBay?" — nothing
   else belongs in it. Suggestions (a missing description, anything the
   server sends as a warning) come after, under their own heading, and are
   hidden entirely while something is still blocking: a seller who can't
   publish should not have to read past advice to find the reason. */
function IssueList({ issues, onFix }) {
  const blocking = issues.filter(isBlocking);
  const advice = issues.filter((it) => !isBlocking(it));
  return (
    <>
      {blocking.length > 0 && (
        <>
          <p className="mt-3 text-[12px] font-bold uppercase tracking-wide text-warning">
            {blocking.length === 1 ? "Blocking this listing" : `Blocking this listing (${blocking.length})`}
          </p>
          <ul className="mt-2 flex flex-col gap-3">
            {blocking.map((it, i) => <IssueRow key={i} it={it} onFix={onFix} />)}
          </ul>
        </>
      )}
      {advice.length > 0 && blocking.length === 0 && (
        <>
          <p className="mt-3 text-[12px] font-bold uppercase tracking-wide text-ink-faint">
            Optional — eBay accepts the listing without these
          </p>
          <ul className="mt-2 flex flex-col gap-3">
            {advice.map((it, i) => <IssueRow key={i} it={it} onFix={onFix} />)}
          </ul>
        </>
      )}
    </>
  );
}

// One row per marketplace after a multi-marketplace publish: ok/fail icon,
// the marketplace's own message, a "View on X" link when it's live, and each
// marketplace's fix-it issues with the same jump buttons the eBay panel has.
function MultiResultPanel({ r, onFix }) {
  const { marketplaces } = useApp();
  const labelFor = (key) =>
    (marketplaces.find((m) => m.key === key) || {}).label || key;
  const entries = Object.entries(r.results || {});
  const anyFail = entries.some(([, res]) => !res.ok);
  return (
    <div className={cn(
      "rounded-tile border p-4",
      anyFail ? "bg-warning-soft border-warning/30"
        : "bg-success-soft border-success/25",
    )}>
      <p className="font-bold text-sm text-ink flex items-center gap-2">
        {anyFail
          ? <AlertTriangle size={17} className="text-warning" aria-hidden />
          : <CheckCircle2 size={17} className="text-success" aria-hidden />}
        {r.message || "Done!"}
      </p>
      <ul className="mt-3 flex flex-col gap-3.5">
        {entries.map(([key, res]) => (
          <li key={key} className="text-sm">
            <p className="font-semibold text-ink flex items-center gap-1.5">
              {res.ok
                ? <CheckCircle2 size={15} className="text-success" aria-hidden />
                : <AlertTriangle size={15} className="text-warning" aria-hidden />}
              {labelFor(key)}
              {res.dry_run && (
                <span className="text-xs font-medium text-ink-faint">dry run</span>
              )}
            </p>
            {res.message && (
              <p className="text-ink-secondary mt-0.5">{res.message}</p>
            )}
            {res.ok && res.url && (
              <a
                href={res.url} target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-1 mt-1 text-[13px] font-semibold text-blue hover:underline"
              >
                View on {labelFor(key)} <ExternalLink size={12} aria-hidden />
              </a>
            )}
            {(res.issues || []).length > 0 && (
              <IssueList issues={res.issues} onFix={onFix} />
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/* "Ask eBay why" — for the one rejection eBay refuses to explain.
 *
 * Error 240 means either "this account is held" or "eBay dislikes the words
 * in this listing", and says which about as often as never. The server can
 * settle it by re-putting the listing through eBay's own dry-run call twice,
 * once with plain wording — but only on the failure path, and only when the
 * account APIs came up empty. This is the same diagnosis on demand, with
 * eBay's answers shown verbatim so they can be quoted to eBay support. */
function AskEbayWhy({ w }) {
  const [state, setState] = useState(null); // null | {loading} | {data} | {error}
  const ask = async () => {
    setState({ loading: true });
    try {
      setState({ data: await postJson("/api/ebay/diagnose-block", {
        session_id: w.sessionId, listing: w.collect(), mode: "live" }) });
    } catch (e) {
      setState({ error: e.message });
    }
  };
  const d = state?.data;
  return (
    <div className="mt-3">
      <Button variant="soft" size="sm" onClick={ask} disabled={state?.loading}>
        <ListChecks aria-hidden />
        {state?.loading ? "Asking eBay…" : "Ask eBay why"}
      </Button>
      {state?.error && (
        <p className="mt-2 text-sm text-ink-secondary">
          Couldn't ask eBay: {state.error}
        </p>
      )}
      {d && (
        <div className="mt-3 text-sm text-ink">
          <p className="font-semibold">{d.verdict || "eBay gave no verdict."}</p>
          <details className="mt-2">
            <summary className="cursor-pointer text-xs font-semibold text-ink-secondary">
              Everything eBay said (for eBay Customer Service)
            </summary>
            <pre className="mt-2 text-xs bg-bg-sunken rounded-[10px] p-3 overflow-x-auto max-h-72 whitespace-pre-wrap">
              {JSON.stringify(d, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </div>
  );
}

/* What the last publish, save, check or dry run came back with, in a
 * panel that rides above the bar.
 *
 * It was the Publish card, the last of ten, at the bottom of the form: the
 * one place a refusal was explained, and the one place a seller had to
 * scroll to find. The bar is pinned, so the answer to "why isn't this on
 * eBay?" is pinned with it now, with the Fix buttons that jump to the
 * section eBay named. Closable: once read, it is in the way. */
function ResultPanel({ w, onFix }) {
  const r = w.publishResult;
  if (!r) return null;
  const multiOk = r.multi && Object.values(r.results || {}).every((res) => res.ok);
  const publishedOk = !r.error && !r.multi
    && (r.published || r.draft || r.ebay_draft || r.dry_run || r.preflight);
  const tone = r.error && !multiOk ? "warn" : "ok";
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      role="region"
      aria-label="Publish result"
      data-publish-result
      className={cn(
        "relative rounded-card border-2 backdrop-blur shadow-float p-4 pr-11 bg-card/95",
        "max-h-[45dvh] overflow-y-auto",
        tone === "warn" ? "border-warning/50" : "border-success/45",
      )}
    >
      <button
        type="button"
        onClick={w.dismissPublishResult}
        aria-label="Close the publish result"
        className="absolute top-3 right-3 grid place-items-center size-8 rounded-full cursor-pointer text-ink-faint hover:text-ink hover:bg-bg-sunken"
      >
        <X size={16} aria-hidden />
      </button>

      {r.multi && <MultiResultPanel r={r} onFix={onFix} />}

      {publishedOk && (
        <div className="flex gap-3">
          <CheckCircle2 size={20} className="text-success shrink-0 mt-0.5" aria-hidden />
          <div className="text-sm text-ink min-w-0">
            {/* Live publishes swap to the PublishedScreen; this covers draft
                saves, dry runs, and preflight results. */}
            <p className="font-semibold">{r.message || "Done!"}</p>
            {r.dry_run && (
              <details className="mt-2">
                <summary className="cursor-pointer text-xs font-semibold text-ink-secondary inline-flex items-center gap-1">
                  <Eye size={12} aria-hidden /> View the exact eBay API payload
                </summary>
                <pre className="mt-2 text-xs bg-bg-sunken rounded-[10px] p-3 overflow-x-auto max-h-72">
                  {JSON.stringify(r.payload, null, 2)}
                </pre>
                {r.export_path && (
                  <p className="text-xs text-ink-faint mt-1.5">Saved to {r.export_path}</p>
                )}
              </details>
            )}
          </div>
        </div>
      )}

      {r.error && !r.multi && (
        <div>
          <p className="font-bold text-sm text-ink flex items-center gap-2">
            <AlertTriangle size={17} className="text-warning" aria-hidden />
            {r.message || "eBay couldn't publish this yet"}
          </p>
          <IssueList
            issues={r.issues?.length
              ? r.issues
              : [{ target: "generic", title: r.message || "eBay rejected the listing", fix: typeof r.detail === "string" ? r.detail : "" }]}
            onFix={onFix}
          />
          {(r.issues || []).some((i) => i.target === "account") && (
            <AskEbayWhy w={w} />
          )}
          {r.detail && (
            <details className="mt-3">
              <summary className="cursor-pointer text-xs font-semibold text-ink-secondary">
                eBay's exact message
              </summary>
              <pre className="mt-2 text-xs bg-bg-sunken rounded-[10px] p-3 overflow-x-auto max-h-60 whitespace-pre-wrap">
                {typeof r.detail === "string" ? r.detail : JSON.stringify(r.detail, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </motion.div>
  );
}

// Where this publish goes: one toggle chip per connected marketplace. The
// chips themselves are MarketTargetChips in publishShared — the drafts grid
// already renders that exact component, and this file used to carry a
// byte-identical second copy. Both read the same remembered selection — one
// value, shared, so a divergence between them could only ever be a bug.
function MarketplaceChips({ w }) {
  const { otherConnected } = usePublishTargets();
  if (!w.chipTargets) return null;
  return (
    <MarketTargetChips
      selected={w.marketTargets}
      toggle={w.toggleMarketTarget}
      otherConnected={otherConnected}
    />
  );
}

/* "Ask AI" — the refine prompt, in the bar.
 *
 * It was a bar of its own under the page title, and the first thing on
 * the page was a box asking what to change about a listing nobody had read
 * yet. It is a button now, and the prompt opens when it is pressed. The
 * input stays MOUNTED while closed (hidden, not unrendered): it is how the
 * app's own tests recognise the editor, and a mounted input keeps whatever
 * was half-typed when the seller closed it. */
function AskAI({ w, open, onClose }) {
  const [prompt, setPrompt] = useState("");
  const apply = async () => {
    const ok = await w.refine(prompt);
    if (ok) { setPrompt(""); onClose(); }
  };
  return (
    <div className={cn("flex items-center gap-2.5", !open && "hidden")} data-ask-ai>
      <Sparkles size={17} className="text-blue shrink-0" aria-hidden />
      <input
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") apply();
          if (e.key === "Escape") onClose();
        }}
        placeholder='Ask AI to change anything — "make the title punchier, price at $45"'
        aria-label="Refine listing with AI"
        className="flex-1 min-w-0 h-10 bg-bg-sunken rounded-input border border-line px-3 text-[14px] placeholder:text-ink-faint focus:border-blue focus:outline-none focus:ring-2 focus:ring-blue/25"
      />
      <Button variant="primary" size="sm" onClick={apply} disabled={!prompt.trim()}>
        Apply
      </Button>
    </div>
  );
}

// PublishBar — the primary action, pinned to the bottom of the workflow so
// Publish is always one tap away instead of stranded at the end of a long
// form. For a LIVE listing the actions flip to revise mode: Update Live
// Listing + End listing (on a published offer any eBay save goes straight to
// the live listing, so a "draft" would mislead).
//
// What the bar reports is ONE thing: what is stopping this listing from
// reaching eBay. It used to count "fields left to finish" off the per-card
// completion map, which swept in anything merely unfinished — so a seller
// chasing that number could tidy every card the bar named and still be told
// the listing wasn't ready. Now it lists w.blockers and nothing else: every
// chip is a field eBay itself refuses the listing over, each carrying its own
// label and jump target, and an empty list means Publish will go through.
//
// Delete, Check and Save to eBay drafts live in the header's ⋯ menu now
// (LISTING_REDESIGN.md, "Sticky bar"): the bar holds the two things a
// seller does on every listing, Done and Publish, plus Ask AI.
export function PublishBar({ w }) {
  const { canPublishLive, setSession, openListings, openSettings,
    health, listingsState, activeBulk } = useApp();
  const { confirm } = useToast();
  const [askOpen, setAskOpen] = useState(false);
  const blockers = w.blockers;
  const ready = blockers.length === 0;
  // Never name a count without SAYING WHICH — these chips name each blocking
  // field and clicking one scrolls straight to it.
  const jumpTo = (target) => {
    w.setFixTarget(null);
    requestAnimationFrame(() => w.setFixTarget(target));
  };
  const onFix = (target) => {
    // The fix lives on Settings: land on the card that holds it.
    if (target === "location") { openSettings("shipping"); return; }
    if (target === "policies") { openSettings("returns-payment"); return; }
    jumpTo(target);
  };

  // A draft is saved on the way out, not discarded: autosave has been
  // sending edits all along (useListingForm) and this sends the rest, so
  // there is nothing to confirm. The question stays for a live listing --
  // its edits go to eBay with Update, never by a timer -- and for a draft
  // whose last save failed, where "discarded" is true again.
  //
  // Where Done lands: back on the batch when the draft was opened out of
  // one (clearing the session alone brings the queue back -- it is still
  // in memory), else the drafts; a live listing goes back to Manage.
  const askCancel = async () => {
    const mustConfirm = w.isLive || w.saveStatus === "off" || w.saveStatus === "error";
    if (mustConfirm) {
      if (!(await confirm({
        title: "Close without saving?",
        message: w.isLive
          ? "Changes you made here since the last update are discarded — the live listing stays as it is on eBay."
          : "Changes since the last save are discarded — the listing stays in Drafts exactly as last saved.",
        confirmLabel: "Close",
      }))) return;
    } else {
      await w.flushSave();
    }
    setSession(null);
    if (activeBulk && !w.isLive) return;
    openListings(w.isLive ? "active" : "drafts");
  };

  const askEnd = async () => {
    // Which of the two endings this is — kept under Inactive for the grace
    // period, or removed on the spot because the record is only a copy of an
    // eBay listing. The server decides; this reads the same rule so the
    // dialog can say which before the call is made. The record is looked up
    // in the store because `keptWhenEnded` asks about the ROW (its id, its
    // local photos), not about the form being edited.
    const item = listingsState.items.find((i) => i.id === w.sessionId)
      || { id: w.sessionId };
    const kept = keptWhenEnded(item);
    const days = endedGraceDays(health);
    if (await confirm({
      title: "End this listing on eBay?",
      message: kept
        ? "It comes off eBay immediately and moves to Inactive, where you can "
          + `relist it. We clear it out after ${days} days.`
        : "It comes off eBay immediately, and this listing is removed from "
          + "the app — it's a copy of your eBay listing, so there's nothing "
          + "of yours in it to keep.",
      confirmLabel: kept ? "End listing" : "End & remove",
      danger: true,
    })) w.endListing();
  };

  // The headline names the obstacle, not the workload: "1 field left to
  // finish" reads like a chore list and says nothing about consequence. A
  // listing that's already live gets its own wording — it plainly IS on eBay,
  // so the thing at risk is the update, not the listing.
  // Named for the marketplaces this publish is going to, never "eBay" by
  // habit: with Etsy among the chips the list includes Etsy's own asks.
  const n = blockers.length;
  const many = n === 1 ? "1 field" : `${n} fields`;
  const names = marketNames(w.chipTargets);
  const status = w.isLive
    ? {
        head: ready ? "Live on eBay" : blockerHeadline(blockers, w.chipTargets),
        sub: ready
          ? "Your edits publish straight to the live listing."
          : "Fix these before updating the live listing.",
      }
    : {
        head: ready ? "Ready to publish"
          : `${many} ${n === 1 ? "is" : "are"} keeping this off ${names}`,
        sub: ready
          ? (canPublishLive
            ? `List it live on ${names} — or press Done and come back to it later.`
            : "Dry-run mode: no eBay connection yet, so Publish generates the exact API payload to inspect.")
          : "That's the whole list — fix them and it goes live.",
      };

  const targetCount = (w.chipTargets || []).length;
  const liveLabel = targetCount > 1
    ? `Publish to ${targetCount} marketplaces`
    : canPublishLive ? "Publish Live" : "Publish";

  return (
    // bottom-20 was measured against a bottom nav that has no home-indicator
    // inset. On every current iPhone that nav is 28px taller, and its raised
    // centre button reaches 20px higher still — right across the bottom of
    // "Publish Live", where a tap starts a NEW listing instead of publishing
    // this one. The bar has to rise by exactly what the nav grew by, so the
    // clearance holds on a phone with an inset and on one without.
    //
    // 6rem, not 5rem, since the thumb bar went to five slots: that put the
    // raised button dead centre, and every control in this bar is full width
    // on a phone, so its midpoint — where a thumb lands, and where `reach`
    // probes — is now exactly the FAB's column. 5rem left the button's bottom
    // edge 7px inside it. It cleared before only because the FAB sat off to
    // one side; that was luck, not clearance, and any nav reshuffle would
    // have spent it. Measured, not guessed: `npm run reach` is the check.
    <div
      data-publish-bar
      className="sticky bottom-[calc(6rem_+_env(safe-area-inset-bottom))] md:bottom-4 z-30 pt-1 flex flex-col gap-2"
    >
      <ResultPanel w={w} onFix={onFix} />
      <div className={cn(
        "rounded-card border-2 backdrop-blur shadow-float p-3.5 sm:p-4 bg-card/95",
        "flex flex-col gap-3",
        ready ? "border-success/45" : "border-warning/50",
      )}>
        <MarketplaceChips w={w} />
        <AskAI w={w} open={askOpen} onClose={() => setAskOpen(false)} />
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <span className="flex items-center gap-3 min-w-0 flex-1">
          <span className={cn(
            "grid place-items-center size-10 rounded-full shrink-0",
            ready ? "bg-success-soft text-success" : "bg-warning-soft text-warning",
          )}>
            {ready
              ? <CheckCircle2 size={20} aria-hidden />
              : <AlertTriangle size={20} aria-hidden />}
          </span>
          <span className="min-w-0">
            <span className="block text-[15px] sm:text-base font-bold text-ink leading-tight truncate">
              {status.head}
            </span>
            {ready ? (
              <span className="block text-[13px] text-ink-secondary leading-snug mt-0.5">
                {status.sub}
              </span>
            ) : (
              <span className="flex flex-wrap items-center gap-1.5 mt-1">
                {blockers.map((b) => (
                  <button
                    key={b.key}
                    type="button"
                    onClick={() => jumpTo(b.target)}
                    title={b.why}
                    className="inline-flex items-center gap-1 rounded-full bg-warning-soft border border-warning/40 px-2.5 py-0.5 text-[12px] font-bold text-warning cursor-pointer hover:border-warning transition-colors"
                  >
                    {b.label} <ArrowRight size={11} aria-hidden />
                  </button>
                ))}
                <span className="text-[12px] text-ink-faint">tap to jump — or press Done and come back</span>
              </span>
            )}
          </span>
        </span>
        {/* flex-wrap, and shrink-0 only from sm up. Every child is a Button,
            and Button's base class is whitespace-nowrap, so with nowrap +
            shrink-0 this row could not give way anywhere: at 375px five
            buttons measured 582px inside a 311px card, which put "Save Draft"
            54px past the right edge and "Publish Live" entirely off-screen.
            The app's primary action was unreachable on every phone size Apple
            sells, and the page scrolled sideways instead of saying so. */}
        <span className="flex flex-wrap items-center justify-end gap-2.5 w-full sm:w-auto sm:shrink-0">
          <Button variant="ghost" size="md" onClick={() => setAskOpen((o) => !o)}
            aria-expanded={askOpen} aria-label="Ask AI to change the listing">
            <Sparkles aria-hidden className="text-blue" /> <span className="hidden sm:inline">Ask AI</span>
          </Button>
          {w.isLive ? (
            <>
              <Button variant="ghost" size="md" onClick={askCancel} aria-label="Cancel editing">
                Cancel
              </Button>
              <Button variant="secondary" size="lg" onClick={askEnd}>
                <Ban aria-hidden /> End
              </Button>
              <Button variant="primary" size="lg" className="flex-1 sm:flex-none" onClick={() => w.publish("live")}>
                <RefreshCw aria-hidden /> Update Live Listing
              </Button>
            </>
          ) : (
            <>
              {/* "Done", not "Cancel": on a draft this saves what is left
                  and closes -- nothing is cancelled. (The live bar keeps
                  Cancel: there, leaving really does drop the edits.) */}
              <Button variant="secondary" size="lg" onClick={askCancel} aria-label="Done editing">
                Done
              </Button>
              <Button variant="primary" size="lg" className="flex-1 sm:flex-none" onClick={() => w.publish("live")}>
                <Rocket aria-hidden /> {liveLabel}
              </Button>
            </>
          )}
        </span>
        </div>
      </div>
    </div>
  );
}

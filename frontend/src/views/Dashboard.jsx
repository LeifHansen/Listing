import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Camera, Upload, PlusCircle, Store, ArrowRight, Rocket, FileText,
  Tags, Coins, Lightbulb, TrendingDown,
  ListChecks, Loader2, RefreshCw, CheckCircle2, Eye, Heart, BarChart3,
  ChevronDown, DollarSign, AlertTriangle, Sparkles, ClipboardCheck,
} from "lucide-react";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { api, pollJob, postJson } from "@/lib/api";
import { readLocal, writeLocal } from "@/lib/localPrefs";
import { Card, SectionHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { StatCard } from "@/components/ui/StatCard";
import { ListingCard } from "@/components/ListingCard";
import { DuplicateListings } from "@/components/DuplicateListings";
import { ListingCardSkeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ListingsIllustration, WelcomeIllustration } from "@/components/ui/illustrations";
import { cn, formatMoney } from "@/lib/utils";
import { DEFAULT_CURRENCY, DEFAULT_SOLD_RANGE, SOLD_RANGES, currencyOf,
         salesSummary } from "@/lib/sales";
import { isDraft, keptWhenEnded, listingsView, recentListings, storeTotal }
  from "@/lib/listingsView";
import { DraftCategoryEdit } from "@/views/listing/CategoryQuickPick";
import { DraftFormatEdit } from "@/views/listing/FormatQuickPick";
import { storeMirrorView } from "@/lib/storeMirror";

// The signed-out / no-suggestions list. A shared frozen constant so clearing
// it during render is a no-op state write when it is already empty, instead of
// a fresh [] that re-renders the whole dashboard. Never mutated.
const NO_INSIGHTS = Object.freeze([]);
// Per-run caps from /api/insights, keyed by rec type (see main._bulk_caps).
// Empty until the fetch lands, and empty is "no cap known" — the group then
// reads as it always did rather than inventing a limit.
const NO_CAPS = Object.freeze({});
// How big each group really is, keyed by rec type (see main's group_totals).
// The recommendations payload is a capped slice PER TYPE, so the rows that
// arrive are the ones that fit, never the count. Empty until the fetch lands,
// and empty means "fall back to the rows", which is what the group did before
// the server counted.
const NO_TOTALS = Object.freeze({});
// The "Finish everything" plan before /api/insights has answered. Zero total
// hides the button rather than offering one that cannot say what it will do.
const NO_PLAN = Object.freeze({ total: 0, enrich: 0, accept: 0 });
// How many of a group ONE tap actually reaches. Both bulk actions fill a
// capped number of listings per run and defer the rest, so the group must not
// promise the whole badge: it asked to confirm 46, quoted the AI cost of 46,
// then ran 25 and reported "1 of 25".
const runSize = (n, cap) => (cap > 0 ? Math.min(n, cap) : n);
// The count a seller is actually agreeing to, with its noun: "46 listings"
// when the run covers the group, "25 of 46 listings" when it doesn't. The
// noun agrees with the number in front of it — "1 of 3 listings", but "1
// listing".
const runCount = (n, total, noun) =>
  `${n < total ? `${n} of ${total}` : n} ${noun}${Math.max(n, total) === 1 ? "" : "s"}`;

// How many listings a group actually covers. `total` is the server's count
// over the WHOLE ranking (main's group_totals); `recs` is the capped slice of
// it that was sent. Never below the rows on screen — a stale total must not
// make a group claim to be smaller than what it is showing.
const groupSize = (group) => Math.max(group.total || 0, group.recs.length);

/* "Fill in details" and "Check details", as ONE group.
   
   They were two, stacked, and the seller read them as one thing twice:
   "Fill in details · 70" over "Check details · 149", each with its own
   header, its own count and — on only one of them — its own button. The
   split is real to the ENGINE (`specifics` is a fill the AI can make;
   `verify` is a note only a person can settle) and it is not a decision the
   seller has to make: both are "this listing's details aren't finished", and
   the one press that finishes them has always covered both (finish-all).
   So the dashboard groups them: one row, one count, one button, and the rows
   behind it when the seller wants to work through them one at a time. */
const DETAILS = "details";
const DETAILS_TYPES = ["specifics", "verify"];

// Icon + tone for each recommendation type from /api/insights.
const REC_ICON = {
  lower_price: TrendingDown,
  finish: PlusCircle, photos: Camera, specifics: ListChecks,
  verify: ClipboardCheck,
  [DETAILS]: ListChecks,
};
const REC_TONE = {
  lower_price: "bg-yellow-soft text-warning",
  finish: "bg-blue-soft text-blue",
  photos: "bg-blue-soft text-blue",
  specifics: "bg-yellow-soft text-warning",
  verify: "bg-yellow-soft text-warning",
  [DETAILS]: "bg-yellow-soft text-warning",
};
// Category headings for the grouped view — the per-rec `label` is an
// imperative for one listing ("Lower the price"); groups need the noun form.
const REC_GROUP_LABEL = {
  lower_price: "Lower prices",
  finish: "Finish & list",
  photos: "Add more photos",
  // "Fill in details" and "Check details" arrive as two types and render as
  // one group (see DETAILS): two halves of finishing a listing's details,
  // with one button that does both.
  [DETAILS]: "Finish details",
};

// One suggestion row: what the listing is, why it is here, and the way in.
//
// No dismiss control, and deliberately none. Every row used to carry an X
// because the list was rebuilt from scratch on every load and could not be
// finished — advice the seller had considered and decided against came back
// for good, so the only way to make it shrink was to hide it. "Finish all"
// is the answer that was missing: the list shrinks because the work gets
// done. Hiding a row would only put back the thing that made the counter
// meaningless — a number that says how much is left while quietly not
// counting the parts the seller waved away.
function RecRow({ rec, openListing }) {
  const Icon = REC_ICON[rec.type] || Lightbulb;
  return (
    <div className="flex items-center gap-3.5 p-4">
      <span className={cn(
        "grid place-items-center size-10 rounded-[13px] shrink-0",
        REC_TONE[rec.type] || "bg-blue-soft text-blue",
      )}>
        <Icon size={19} strokeWidth={2} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-semibold text-sm text-ink truncate">{rec.listing_title}</p>
        <p className="text-[13px] text-ink-secondary">{rec.reason}</p>
      </div>
      <Button variant="soft" size="sm" className="shrink-0 -mr-1"
        onClick={() => openListing(rec.listing_id)}>
        {rec.label} <ArrowRight aria-hidden />
      </Button>
    </div>
  );
}

// The group-level verbs. A suggestion category earns an entry here when the
// same edit makes sense across every listing in it — repeating one edit a dozen
// times by hand is the whole problem. `amount` marks the ones that need a
// number first (lower prices by HOW much); the rest fire on click.
//
// Photos and finish are deliberately absent: photos need a human holding the
// item, and finishing a draft creates a listing, which is not something to hand
// a single button.
const BULK_ACTIONS = {
  // Finishing a listing's details used to be a prompt to go and do it: open
  // each one, wait for the AI to read its photos, save, repeat. It is the
  // same edit every time and the AI already knows how to make it, so it is a
  // button — eBay's recommended item specifics filled from each listing's own
  // photos and pushed to the live listing, and the notes the AI left for a
  // person marked as checked on the rest.
  //
  // ALL of them. The verb said "all" and meant "a capped 25 of the 50 rows
  // this screen happens to be holding", which on a list of 70 was three
  // presses that each reported what was left — and with BULK_ENRICH_CAP set
  // to 1 in an environment, one listing per press. This run names no ids and
  // has no cap: the server works the set out from the same ranking the
  // screen is rendered from, so "all" is the badge's own number.
  [DETAILS]: {
    verb: "Enrich all",
    icon: Sparkles,
    run: (ctx) => ctx.finishEverything(),
  },
  lower_price: {
    verb: "Lower all…",
    icon: TrendingDown,
    amount: {
      unit: "%", initial: 10, min: 1, max: 75, step: 1,
      label: "Lower every price in this group by",
      submit: (n, value, total) =>
        `Lower ${runCount(n, total, "price")} by ${value}%`,
      note: "New prices go straight to eBay. Anything that has sold or ended is skipped.",
    },
    run: (ctx, value) => ctx.lowerAll(ctx.group, value),
  },
};

// "Lower every price in this group by [ 10 ]%" — the amount an action needs
// before it can run, with its own submit. Rendered in normal flow under the
// group header rather than as a floating panel: the suggestions Card clips
// overflow, so anything absolutely positioned inside it gets cut off.
function BulkAmountPanel({ amount, count, total, busy, onSubmit, onCancel }) {
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
      <div className="mx-4 mb-4 rounded-[13px] border border-line bg-bg-sunken p-3.5">
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
        <p className="mt-2 text-[12px] text-ink-secondary">{note}</p>
        {/* The server reprices a capped number per run and defers the rest.
            Said here, next to the button that spends it, rather than only in
            the toast that arrives once it is already too late to plan. */}
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

// The icon, name and count every group header shows — shared so the version
// inside the expand toggle and the plain one on a button-only group stay the
// same thing.
function GroupHead({ group, Icon }) {
  return (
    <>
      <span className={cn(
        "grid place-items-center size-10 rounded-[13px] shrink-0",
        REC_TONE[group.type] || "bg-blue-soft text-blue",
      )}>
        <Icon size={19} strokeWidth={2} aria-hidden />
      </span>
      <span className="font-semibold text-sm text-ink truncate">
        {REC_GROUP_LABEL[group.type] || group.recs[0].label}
      </span>
      {/* The group's real size, not the number of rows that came down the
          wire. They are the same thing only on a small store: /api/insights
          ships a capped slice per type, so a seller with 80 listings needing
          the fill was shown 50, filled 25 of them, and found the badge still
          reading 50 — 25 that had been below the cap had taken their place.
          The work happened and the number could not show it. */}
      <span className="grid place-items-center font-display tabular-nums text-[11px] font-bold rounded-full bg-bg-sunken px-1.5 min-w-5 h-5 text-ink-secondary">
        {groupSize(group)}
      </span>
    </>
  );
}


// One suggestion category: a collapsed header (icon, label, count) that
// expands to the full row list. Collapsed by default — eight "Lower the
// price" rows read as clutter; one "Lower prices · 8" reads as a to-do.
function RecGroup({ group, cap, openListing, lowerAll, finishEverything,
                    busy, progress }) {
  const [open, setOpen] = useState(false);
  const [amountOpen, setAmountOpen] = useState(false);
  const Icon = REC_ICON[group.type] || Lightbulb;
  const action = BULK_ACTIONS[group.type];
  const ActionIcon = action?.icon;
  // What one tap on this group's button reaches. The badge above it is the
  // whole group; this is the part of it a single run touches.
  const total = groupSize(group);
  const perRun = runSize(total, cap);
  return (
    <div>
      <div className="flex items-center gap-2 pr-4">
        {/* Every group opens. The fill used to render its header as plain
            text — its button was the whole point, and there was nothing to
            choose between — but the two groups it is now one half of pull in
            opposite directions there: the other half is a list of notes only
            a person can settle, one listing at a time. So the button is for
            all of it and the chevron is for going through it. */}
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="flex-1 min-w-0 flex items-center gap-3.5 p-4 text-left cursor-pointer"
        >
          <GroupHead group={group} Icon={Icon} />
          <motion.span
            animate={{ rotate: open ? 180 : 0 }}
            transition={{ duration: 0.18 }}
            className="ml-auto text-ink-faint shrink-0"
          >
            <ChevronDown size={17} aria-hidden />
          </motion.span>
        </button>
        {/* Sibling of the toggle, never nested inside it (invalid HTML). */}
        {action && (
          <Button variant="soft" size="sm" className="shrink-0"
            loading={busy} disabled={busy}
            aria-expanded={action.amount ? amountOpen : undefined}
            onClick={() => (action.amount
              ? setAmountOpen((o) => !o)
              : action.run({ group, cap, lowerAll, finishEverything }))}>
            <ActionIcon aria-hidden /> {action.verb}
          </Button>
        )}
      </div>
      {/* What a long run is actually doing. "Enrich all" is minutes of AI
          reading photos, one listing at a time, and a spinner with no end in
          sight is the shape of a hang — so the group says which listing it is
          on and how far through it is. */}
      {progress && (
        <p className="px-4 pb-3.5 -mt-1 text-[13px] text-ink-secondary flex items-center gap-1.5">
          <Loader2 size={14} className="animate-spin shrink-0" aria-hidden />
          <span className="truncate">
            {progress.title ? `“${progress.title}” · ` : ""}
            {Math.min(progress.done + 1, progress.total)} of {progress.total}
            {/* A capped run has to account for its remainder right here, or
                "2 of 25" under a badge reading 46 reads as a contradiction.
                The fill no longer has one — it takes the whole list — and
                the price drop still does. */}
            {progress.deferred > 0
              ? ` · ${progress.deferred} more after this run` : ""}
          </span>
        </p>
      )}
      <AnimatePresence initial={false}>
        {action?.amount && amountOpen && (
          <BulkAmountPanel
            amount={action.amount} count={perRun} total={total}
            busy={busy}
            onCancel={() => setAmountOpen(false)}
            onSubmit={(value) => {
              setAmountOpen(false);
              action.run({ group, cap, lowerAll, finishEverything }, value);
            }} />
        )}
      </AnimatePresence>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="overflow-hidden"
          >
            {/* One row per listing, so a seller who would rather go through
                them one at a time can — which is the whole reason the merged
                group kept its list instead of inheriting the fill's
                button-only header. */}
            <div className="divide-y divide-line border-t border-line">
              {group.recs.map((rec) => (
                <RecRow key={`${rec.listing_id}-${rec.type}`} rec={rec}
                  openListing={openListing} />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return "Burning the midnight oil";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

function isToday(iso) {
  if (!iso) return false;
  const d = new Date(iso);
  const now = new Date();
  return d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
}

const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.05 } },
};
const rise = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { duration: 0.25, ease: "easeOut" } },
};

// One line of truth about the mirror: syncing / synced / not connected. Lives
// in the hero so "is this my real store?" is answered before anything else.
function MirrorStatus() {
  const { user, ebay, storeSync, setView } = useApp();
  const mirror = storeMirrorView({
    user, connected: ebay.connected, ...storeSync,
  });
  if (mirror.kind === "hidden") return null;
  if (mirror.kind === "not-connected") {
    return (
      <button
        type="button"
        onClick={() => setView("settings")}
        className="inline-flex items-center gap-1.5 text-[13px] font-medium text-warning cursor-pointer hover:underline"
      >
        <Store size={14} aria-hidden /> Connect eBay in Settings to mirror your store here
      </button>
    );
  }
  if (mirror.kind === "syncing") {
    // A store of any size takes minutes (one eBay call per listing), so the
    // line carries the count the background job reports — a bare spinner with
    // no end in sight reads as broken.
    // One count, because it is now one pass: each listing is fetched from
    // eBay and written down as a single unit, so there is no longer a
    // "fetching" stage followed by a "saving" one to tell apart. The number
    // is how many of the seller's listings are actually here.
    const p = storeSync.progress;
    const detail = p && p.total
      ? ` ${Math.min(p.done, p.total)} of ${p.total} listings`
      : "";
    return (
      <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-ink-secondary">
        <Loader2 size={14} className="animate-spin" aria-hidden />
        {` Syncing your eBay store…${detail}`}
      </span>
    );
  }
  if (mirror.kind === "error") {
    return (
      <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-warning">
        <RefreshCw size={14} aria-hidden /> Store sync hit a snag — retry from Listings
      </span>
    );
  }
  // A partial pass is not a failure and must not read as one — the records
  // below are real, they are just not all of them and not all freshly
  // checked. Same icon in a quieter colour, and the certainty removed from
  // both the line and its tooltip. See lib/storeMirror.
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-ink-secondary"
      title={mirror.title}>
      <CheckCircle2 size={14}
        className={mirror.kind === "partial" ? "text-ink-faint" : "text-success"}
        aria-hidden /> {mirror.text}
    </span>
  );
}

// The sold tile's window. Remembered across visits — a seller who thinks in
// months shouldn't have to re-pick every morning. Rendered as a plain select:
// it lives in the tile's corner as a sibling of the tile button (see
// StatCard's `action`), so it has to be a real control, not a nested one.
const SOLD_RANGE_KEY = "sold-range";   // see lib/localPrefs

function readSoldRange() {
  try {
    const saved = readLocal(SOLD_RANGE_KEY);
    if (SOLD_RANGES.some((r) => r.id === saved)) return saved;
  } catch (e) { /* private mode — the default is fine */ }
  return DEFAULT_SOLD_RANGE;
}

// The sold tile's second line. It has one job per state: what the total is
// made of, or — when nothing sold in the window — what to do instead.
//
// The relist nudge counts the seller's OWN ended listings and not the store's
// mirrors of eBay's: a mirror is removed as soon as we know it ended, so it
// is never something to relist, and counting one would offer an action on a
// card that is already gone.
function soldSub(sales, ended) {
  if (!sales.count) {
    // An undated sale is one the app knew about before it started recording
    // sale dates — a store sync backfills them from eBay's own dates.
    if (sales.undated) {
      return `sync your store to date ${sales.undated} past sale${sales.undated === 1 ? "" : "s"}`;
    }
    return ended
      ? `nothing in the ${sales.range.long} · ${ended} to relist`
      : `nothing in the ${sales.range.long}`;
  }
  // The window itself is named by the picker in the corner, so this line
  // spends its width on what the total is made of instead of repeating it.
  const parts = [`${sales.count} sale${sales.count === 1 ? "" : "s"}`];
  // Only when there is one currency to name it in. Profit summed across
  // currencies is a number with no meaning, and printing it with a default
  // dollar sign is how a seller on eBay.co.uk was told their £45 item sold
  // "for $45.00" -- fixed once, at the sold notification, and still here.
  if (sales.profit != null && sales.currency) {
    const sign = sales.profit >= 0 ? "+" : "−";
    parts.push(`${sign}${formatMoney(Math.abs(sales.profit), sales.currency)} profit`);
  }
  if (sales.mixedCurrency) parts.push("more than one currency");
  // Say when the total is leaning on asking prices rather than reported sale
  // amounts, instead of presenting a guess as the takings.
  if (sales.approx) parts.push(`${sales.approx} estimated`);
  return parts.join(" · ");
}

function SoldRangePicker({ value, onChange }) {
  return (
    <select
      value={value}
      aria-label="Time range for the sold total"
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        "appearance-none rounded-full border border-line bg-bg-sunken",
        "px-2.5 py-1 pr-6 text-[11px] font-bold text-ink-secondary cursor-pointer",
        "bg-[length:9px] bg-[right_8px_center] bg-no-repeat",
        "focus:outline-none focus:ring-2 focus:ring-blue/40",
      )}
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' fill='none' stroke='%2394a3b8' stroke-width='1.6' stroke-linecap='round'/%3E%3C/svg%3E\")",
      }}
    >
      {SOLD_RANGES.map((r) => (
        <option key={r.id} value={r.id}>{r.label}</option>
      ))}
    </select>
  );
}

export function Dashboard() {
  const { user, openAuth, listingsState, loadListings, startNew, openListing, setView, openListings, session, deleteListing, rotateListingPhoto, metricsById, metricsStatus, ebay, loadEbayStatus, tokens, loadTokens } = useApp();
  const { confirm, toast } = useToast();
  const items = listingsState.items;
  const storeView = listingsView({
    ...listingsState, user, count: items.length,
  });

  // "What to do next" — ranked actions across the user's listings.
  const [insights, setInsights] = useState(NO_INSIGHTS);
  // How many listings one run of each group's bulk action reaches, straight
  // from the server that enforces it — the dashboard cannot guess it, and
  // guessing wrong is how the group came to promise 46 and deliver 25.
  const [bulkCaps, setBulkCaps] = useState(NO_CAPS);
  // How many listings each suggestion group really covers — the count the
  // badge shows. Separate from the rows because it is a different question:
  // the rows are a capped slice per type, and counting them is how "Enrich
  // all" came to fill 25 listings and leave the badge exactly where it was.
  const [groupTotals, setGroupTotals] = useState(NO_TOTALS);
  // What one press of "Finish everything" would do, split by what it costs
  // (see main._finish_all_plan). `enrich` is the listings the AI has never
  // read — those are the ones charged for; `accept` have been read already,
  // so all that is left on them is the notes, and saying "these are fine" is
  // free. Quoting the total as though it all costs would price a 308-listing
  // press at four times what it spends.
  const [finishPlan, setFinishPlan] = useState(NO_PLAN);
  // Signing out throws the suggestions away — they are one account's to-do
  // list, and eBay actions fire straight off them. That reset used to sit at
  // the top of `refreshInsights`, which made it a setState inside the effect
  // below: a cascading render, and one commit late, so the previous account's
  // suggestions stayed on screen (and stayed clickable) for a frame. React's
  // documented alternative is to adjust state DURING render — same shape as
  // the metrics reset in store.jsx. Only the reset moved; the fetch still
  // lives in the effect, and `refreshInsights` is still the single thing the
  // action handlers call after they change eBay.
  //
  // The condition is "there is nobody to show these to", NOT "the moment the
  // user went away": the effect below cleared on EVERY run while signed out,
  // and an edge-triggered version (compare against the previous `user`) would
  // lose that. The `/api/insights` fetch has no abort — a response sent for
  // the old session can resolve after the logout render and repopulate the
  // list, and past that edge nothing would ever clear it again, leaving one
  // account's listings (with live Lower-price / Enrich buttons) on a
  // signed-out dashboard. Level-triggering costs an identity check and
  // converges: once `insights` is the shared empty, the write is skipped.
  if (!user && insights !== NO_INSIGHTS) setInsights(NO_INSIGHTS);
  const refreshInsights = useCallback(() => {
    if (!user) return;
    api("/api/insights")
      .then((r) => {
        setInsights(r.recommendations || NO_INSIGHTS);
        setBulkCaps(r.bulk_caps || NO_CAPS);
        setGroupTotals(r.group_totals || NO_TOTALS);
        setFinishPlan(r.finish_all || NO_PLAN);
      })
      .catch(() => {});
  }, [user]);
  // WHAT the store is, not how big it is. This used to re-read the
  // suggestions when the item COUNT changed, and a store that changes without
  // changing size — a live listing sells while a draft is published, an ended
  // one is relisted — kept the to-do list, group counts and all, that was
  // built for the store it used to be. Status is in the key because that is
  // what nearly every suggestion turns on. Same fix, for the same reason, as
  // the metrics fetch in store.jsx.
  const storeShape = useMemo(
    () => items.map((i) => `${i.id}:${i.status}`).join("|"), [items]);
  useEffect(() => { refreshInsights(); }, [refreshInsights, storeShape]);

  // Bulk price drop across one suggestion group. Reports per-listing outcomes
  // rather than a bare success: over a dozen listings some will have sold or
  // ended since the suggestion was computed, and "lowered 11, skipped 1" is
  // the honest answer.
  const [bulkBusy, setBulkBusy] = useState(null); // group type, or null
  const lowerAll = async (group, percent) => {
    const ids = group.recs.map((r) => r.listing_id);
    // The rows the group holds are a capped slice of it (see groupSize), and
    // the ones that did not fit are still prices this button has to get to.
    const unsent = Math.max(groupSize(group) - ids.length, 0);
    setBulkBusy(group.type);
    try {
      const res = await postJson("/api/ebay/lower-prices",
        { percent, listing_ids: ids });
      const parts = [];
      if (res.changed) parts.push(`Lowered ${res.changed} price${res.changed === 1 ? "" : "s"} by ${percent}%`);
      if (res.skipped) parts.push(`${res.skipped} skipped`);
      if (res.failed) parts.push(`${res.failed} failed`);
      // The server caps one run so the request can't outlive the gateway —
      // and it can only defer what it was sent, so the rest of the group is
      // added back on here.
      const left = unsent + (res.deferred || 0);
      if (left) parts.push(`${left} left — run it again to finish`);
      toast(parts.join(" · ") || "Nothing to change.", {
        kind: res.changed ? "success" : res.failed ? "error" : "info",
      });
      refreshInsights();
      loadListings({ quiet: true });
    } catch (e) {
      toast(`Couldn't lower prices: ${e.message}`, { kind: "error" });
    } finally { setBulkBusy(null); }
  };

  // What a long run is doing right now, or null. Both the fill and the
  // whole-list press are background JOBS the client polls — minutes of vision
  // passes and eBay revises, far longer than any browser holds a request open
  // — and a spinner with no end in sight is the shape of a hang. `type` says
  // which control the line belongs under: a group's own rec type, or
  // FINISH_ALL for the press that spans them.
  const [bulkProgress, setBulkProgress] = useState(null);

  // "Finish everything" — the whole suggestions list, in one press.
  //
  // The seller was looking at "Fill in details · 131" above "Check details ·
  // 177" and said: "I want all of this to be done and submitted to eBay with
  // one click, not individually, and not broken out into multiple steps."
  // Every word of that named something real. There was no button spanning
  // both groups; "Check details" had no bulk verb at all, only 177 rows each
  // opening one listing; and "Enrich all" ran a capped 25 and deferred the
  // rest, so 131 was six presses.
  //
  // So this one sends NO ids. Every other bulk action here hands the server
  // the group's membership, which is right when the client is naming a
  // selection and wrong here: the recommendations payload is a capped slice
  // per type, so a client-named set could only reach the rows it happens to
  // have been sent. The server works the set out from the same ranking this
  // screen is rendered from, which is what makes "it clears the list" true
  // rather than approximately true.
  const finishEverything = async () => {
    const plan = finishPlan;
    if (!plan.total) return;
    // Same reason as the fill: these listings are live on eBay, and filling
    // one means revising it there. Asked of the SERVER, not the cached flag,
    // so a seller who pressed early isn't bounced to Settings.
    if (!ebay.connected) {
      const fresh = await loadEbayStatus();
      if (fresh && !fresh.connected) {
        toast("Connect eBay first — these listings are live there, so filling "
          + "them in means updating them on eBay.", { kind: "warning" });
        setView("settings");
        return;
      }
    }
    const cost = tokens.enabled && tokens.costs?.specifics && plan.enrich
      ? ` It uses ${tokens.costs.specifics * plan.enrich} AI tokens (${tokens.costs.specifics} per listing); you have ${tokens.total}.`
      : "";
    // Said plainly, because the two halves are not the same promise and the
    // seller is agreeing to both: one spends money and changes the live
    // listing, the other retires a nag.
    const fills = plan.enrich
      ? `The AI reads the photos on ${plan.enrich} listing${plan.enrich === 1 ? "" : "s"} `
        + "it hasn't read yet, fills in eBay's recommended item specifics, and "
        + "pushes them straight to the live listing. "
      : "";
    const accepts = plan.accept
      ? `On the other ${plan.accept}, it has already looked and left notes only `
        + "you can settle — a measurement, a signature. Those get marked as "
        + "checked so they stop asking. The notes stay on the listing and "
        + "nothing about them goes to eBay. "
      : "";
    if (!(await confirm({
      title: `Finish all ${plan.total} listing${plan.total === 1 ? "" : "s"}?`,
      message: `${fills}${accepts}Anything you've already written is left `
        + "exactly as it is. This runs in the background — you can keep "
        + `working while it does.${cost}`,
      confirmLabel: "Finish them",
    }))) return;
    setBulkBusy(DETAILS);
    setBulkProgress({ type: DETAILS, done: 0, total: plan.total,
                      deferred: 0, title: "" });
    try {
      const start = await postJson("/api/listings/finish-all", {});
      const total = start.total || plan.total;
      const res = await pollJob(start.job_id, {
        onUpdate: (j) => setBulkProgress({
          type: DETAILS, done: j.current || 0,
          total: j.total_items || total, deferred: 0,
          title: j.current_title || "",
        }),
      });
      const parts = [];
      if (res.changed) {
        parts.push(`Filled in ${res.changed} listing${res.changed === 1 ? "" : "s"}`
          + (res.filled ? ` · ${res.filled} detail${res.filled === 1 ? "" : "s"} added` : ""));
      }
      if (res.accepted) parts.push(`${res.accepted} marked as checked`);
      // The honest remainder. A listing whose photos are no longer on the
      // server has not been read and the fill is still ahead of it, so it
      // stays on the list — and saying so is the difference between a button
      // that fell short and one that quietly lied about finishing.
      if (res.skipped) parts.push(`${res.skipped} still need you`);
      if (res.failed) parts.push(`${res.failed} failed`);
      if (res.stopped) parts.push(res.stopped);
      const results = res.results || {};
      const undone = [...(results.skipped || []), ...(results.failed || [])];
      const lines = undone.slice(0, 3).map(
        (r) => `• ${r.title || "A listing"}: ${r.message}`);
      const more = undone.length - lines.length;
      if (more > 0) lines.push(`• …and ${more} more`);
      toast([parts.join(" · ") || "Nothing left to do.", ...lines].join("\n"), {
        kind: res.failed ? "error" : "success",
        ttl: lines.length ? 12000 : undefined,
      });
      refreshInsights();
      loadListings({ quiet: true });
      loadTokens();
    } catch (e) {
      toast(`Couldn't finish your list: ${e.message}`, { kind: "error" });
    } finally {
      setBulkBusy(null);
      setBulkProgress(null);
    }
  };

  /* The capped, client-named fill that used to sit on "Fill in details" is
     gone from this screen. It handed the server the ids the recommendations
     payload happened to carry (50 per type at most) and the server filled a
     capped 25 of those, so the button could not finish a list bigger than
     itself — and with BULK_ENRICH_CAP set to 1 in an environment it filled
     exactly one listing per press while reporting "69 more after this run".
     `finishEverything` above is what the group runs now: no ids, no cap, the
     set worked out server-side from the same ranking this screen renders.
     POST /api/listings/enrich is still there for a caller that genuinely
     means "these ids, capped" — nothing in the app does. */

  const askDelete = async (item) => {
    const name = item.listing?.title || item.title || "this listing";
    if (await confirm({
      title: "Delete this listing?",
      message: `"${name}" will be permanently removed. This can't be undone.`,
      confirmLabel: "Delete",
      danger: true,
    })) deleteListing(item.id);
  };

  // Only listings actually made IN the app count as "created today".
  // Imported rows (id "ebay-...") get their created_at at sync time — and
  // eBay auto-relists mint new item ids, so every sync would otherwise
  // claim the user "created" a pile of listings they never touched.
  const todays = items.filter(
    (i) => isToday(i.created_at) && !String(i.id).startsWith("ebay-"));
  const drafts = items.filter((i) => i.status === "draft" || i.status === "dry_run");
  const live = items.filter((i) => i.status === "published" || i.status === "live");
  const inventory = items.filter((i) => i.status === "unlisted");
  // Ended and still here, which after the automatic removal means one of the
  // seller's own inside its grace period — something they can still relist.
  const relistable = items.filter(
    (i) => i.status === "ended" && keptWhenEnded(i)).length;
  // Sold revenue over the chosen window. What the buyers actually PAID —
  // an accepted offer settles below the asking price, and totalling `price`
  // would report money that never arrived. See lib/sales.
  const [soldRangeId, setSoldRangeId] = useState(readSoldRange);
  const sales = salesSummary(items, soldRangeId);
  const pickSoldRange = (id) => {
    setSoldRangeId(id);
    writeLocal(SOLD_RANGE_KEY, id);
  };
  const revenue = live.reduce((sum, i) => sum + (Number(i.listing?.price) || 0), 0);
  // The same question for the live listings: one currency, or a sum that
  // cannot be shown as money at all.
  const liveMoney = currencyOf(live);
  const watcherTotal = live.reduce((sum, i) => {
    const m = metricsById[i.id];
    const w = m?.watchers ?? i.listing?.watch_count ?? 0;
    return sum + (Number(w) || 0);
  }, 0);

  // An in-memory session resumes directly — but NOT once it's gone live (or
  // otherwise left the draft stage): there's nothing to "continue" on a
  // published listing, so fall through to the newest actual draft instead.
  //
  // EITHER record saying it is done ends it. `session.status || item.status`
  // read only the session's, because a session that has one at all (any
  // listing opened from Drafts carries "draft") short-circuited the fallback
  // — so a draft opened, published and left behind kept its "Continue" button
  // for the rest of the visit, pointing at a live listing.
  const sessionItem = session ? items.find((i) => i.id === session.sessionId) : null;
  const DONE = ["published", "live", "sold", "ended"];
  const sessionDone = DONE.includes(session?.status)
    || DONE.includes(sessionItem?.status);
  const lastOpen = (session && !sessionDone)
    ? { title: session.listing?.title, go: () => setView("new") }
    : (drafts[0] && {
        title: drafts[0].listing?.title || drafts[0].title,
        go: () => openListing(drafts[0].id),
      });

  // The four listings the seller can still act on that are most worth acting
  // on. Sold ones are left out, the same way the Sell screen's tabs leave them
  // out: a sale is archived under Inactive there, but this strip sorted the
  // whole store by `updated_at` and a sale is the last thing that touches a
  // row — so the item that had just left the Sell screen went straight to the
  // top of the dashboard, took a quarter of the strip, and pushed a live
  // listing off it.
  //
  // The metrics are passed for the other half of that: a bid or an offer
  // holds the front of the strip, rather than the last bulk enrich or batch
  // of relists — which stamp `updated_at` on everything they touch — walking
  // it off the end. Same rule, same function, as the grid on the Sell screen.
  const recent = recentListings(items, metricsById);

  const quickActions = [
    { label: "Take Photos", icon: Camera, onClick: startNew, tone: "bg-blue-soft text-blue" },
    { label: "Upload Images", icon: Upload, onClick: startNew, tone: "bg-green-soft text-green" },
    { label: "Create Listing", icon: PlusCircle, onClick: startNew, tone: "bg-yellow-soft text-warning" },
    { label: "Shop Mode", icon: Store, onClick: () => setView("shop"), tone: "bg-red-soft text-error" },
  ];

  return (
    <motion.div variants={stagger} initial="hidden" animate="show" className="flex flex-col gap-6">
      {/* Hero */}
      <motion.div variants={rise}>
        <Card className="relative overflow-hidden p-7 sm:p-8">
          <div className="flex flex-wrap items-start gap-6 justify-between">
            <div className="min-w-0">
              <h1 className="text-2xl sm:text-[28px] font-bold text-ink">
                {greeting()}{user ? `, ${user.display_name || user.email.split("@")[0]}` : ""} 👋
              </h1>
              <p className="mt-1.5 text-[15px] text-ink-secondary">
                {todays.length > 0
                  ? <>You've created <strong className="text-ink">{todays.length}</strong> listing{todays.length === 1 ? "" : "s"} today.</>
                  : "Ready to flip something today?"}
                {drafts.length > 0 && <> {drafts.length} draft{drafts.length === 1 ? "" : "s"} waiting.</>}
              </p>
              <div className="mt-2"><MirrorStatus /></div>
              <div className="mt-5 flex flex-wrap items-center gap-2.5">
                {lastOpen ? (
                  <Button variant="primary" size="lg" onClick={lastOpen.go} className="max-w-full">
                    <span className="truncate">
                      Continue "{lastOpen.title || "last listing"}"
                    </span>
                    <ArrowRight aria-hidden className="shrink-0" />
                  </Button>
                ) : (
                  <Button variant="primary" size="lg" onClick={startNew}>
                    <PlusCircle aria-hidden /> Create a listing
                  </Button>
                )}
                {!user && (
                  <Button variant="ghost" size="lg" onClick={() => openAuth()}>
                    Log in to save your work
                  </Button>
                )}
              </div>
            </div>
            <div className="hidden lg:block shrink-0 -my-2">
              <WelcomeIllustration />
            </div>
          </div>
        </Card>
      </motion.div>

      {/* Quick actions */}
      <motion.div variants={rise} className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {quickActions.map((a) => (
          <motion.button
            key={a.label}
            type="button"
            onClick={a.onClick}
            whileHover={{ y: -2, boxShadow: "var(--shadow-card-hover)" }}
            whileTap={{ scale: 0.98 }}
            transition={{ duration: 0.15 }}
            className="bg-card border border-line rounded-card shadow-card p-5 flex items-center gap-3.5 cursor-pointer text-left"
          >
            <span className={`grid place-items-center size-11 rounded-[14px] shrink-0 ${a.tone}`}>
              <a.icon size={21} strokeWidth={2} aria-hidden />
            </span>
            <span className="font-semibold text-sm text-ink">{a.label}</span>
          </motion.button>
        ))}
      </motion.div>

      {/* Your store, mirrored — the tiles are the seller's REAL numbers and
          each one jumps to the matching view. */}
      <motion.div variants={rise} className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Every one of these is counted off `items`, so when the store read
            fails they all count zero and say so as a fact -- on the same
            screen, at the same moment, as the card below explaining that the
            listings could not be loaded. storeTotal is what stops that. */}
        <StatCard icon={Coins} tone="green" label="Active on eBay"
          {...storeTotal(storeView.kind, live.length,
            revenue > 0 && liveMoney.currency
              ? `${formatMoney(revenue, liveMoney.currency)} listed${watcherTotal ? ` · ${watcherTotal} watcher${watcherTotal === 1 ? "" : "s"}` : ""}`
              : "everything currently live")}
          onClick={() => openListings("active")} />
        <StatCard icon={FileText} tone="yellow" label="Drafts in progress"
          {...storeTotal(storeView.kind, drafts.length, inventory.length
            ? `+ ${inventory.length} unlisted find${inventory.length === 1 ? "" : "s"} from Shop Mode`
            : "open one to finish & publish")}
          onClick={() => openListings("drafts")} />
        <StatCard icon={DollarSign} tone="blue" label="Sold"
          {...storeTotal(storeView.kind,
                         // A dash for a total that spans currencies, the same
                         // answer this tile gives for one it could not
                         // measure -- because a mixed sum is not a figure the
                         // seller can act on whatever symbol goes in front.
                         sales.mixedCurrency
                           ? "—"
                           : formatMoney(sales.total,
                                         sales.currency || DEFAULT_CURRENCY)
                             || "$0.00",
                         soldSub(sales, relistable))}
          action={<SoldRangePicker value={soldRangeId} onChange={pickSoldRange} />}
          onClick={() => openListings("inactive")} />
        <StatCard icon={Rocket} tone="red" label="Listed today"
          {...storeTotal(storeView.kind, todays.length,
                         todays.length ? "keep the streak going"
                                       : "photos in, listing out — ~30s")}
          onClick={startNew} />
      </motion.div>

      {/* Traffic — real eBay numbers for the live listings (Sell Analytics
          views/impressions over 90 days — eBay's longest report — + watchers),
          with the top performer.
          Views/impressions need eBay's Sell Analytics permission: a seller who
          connected before the app asked for it keeps the original grant
          through every token refresh, so the report 401/403s. Say that plainly
          rather than printing a 0 the seller would read as "nobody looked". */}
      {(() => {
        const withMetrics = live.filter((i) => metricsById[i.id]);
        const trafficOk = metricsStatus.trafficOk;
        const needsReconnect = ebay.connected && live.length > 0
          && metricsStatus.needsReconnect;
        if (!withMetrics.length && !needsReconnect) return null;
        const views = withMetrics.reduce((s, i) => s + (metricsById[i.id].views || 0), 0);
        const impressions = withMetrics.reduce((s, i) => s + (metricsById[i.id].impressions || 0), 0);
        const top = [...withMetrics].sort((a, b) =>
          (metricsById[b.id].views || 0) - (metricsById[a.id].views || 0))[0];
        const topViews = top ? (metricsById[top.id].views || 0) : 0;
        return (
          <motion.div variants={rise}>
            <Card className="py-3.5 px-5 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[13px] text-ink-secondary">
              <span className="inline-flex items-center gap-1.5 font-semibold text-ink">
                <BarChart3 size={15} className="text-blue" aria-hidden /> Traffic · 90 days
              </span>
              {trafficOk && (
                <span className="inline-flex items-center gap-1 tabular-nums">
                  <Eye size={14} aria-hidden /> {views.toLocaleString()} views
                </span>
              )}
              {trafficOk && impressions > 0 && (
                <span className="tabular-nums">{impressions.toLocaleString()} search impressions</span>
              )}
              {withMetrics.length > 0 && (
                <span className="inline-flex items-center gap-1 tabular-nums">
                  <Heart size={14} aria-hidden /> {watcherTotal.toLocaleString()} watchers
                </span>
              )}
              {trafficOk && topViews > 0 && (
                <button
                  type="button"
                  onClick={() => openListing(top.id)}
                  className="inline-flex items-center gap-1 min-w-0 max-w-full text-blue font-semibold cursor-pointer hover:underline"
                >
                  <span className="truncate">
                    Top: “{top.listing?.title || top.title || "Untitled"}”
                  </span>
                  <span className="shrink-0 tabular-nums">({topViews.toLocaleString()})</span>
                </button>
              )}
              {needsReconnect && (
                <span className="inline-flex flex-wrap items-center gap-1.5">
                  eBay won’t share your views and impressions with this app yet.
                  <button
                    type="button"
                    onClick={() => setView("settings")}
                    className="text-blue font-semibold cursor-pointer hover:underline"
                  >
                    Reconnect eBay to see them
                  </button>
                </span>
              )}
            </Card>
          </motion.div>
        );
      })()}

      {/* Possible duplicates — leftovers of the publish race the app now
          prevents. Above the suggestions because a duplicate live listing
          costs money and risks eBay's duplicate-listing policy, and because
          the card hides itself entirely when there's nothing to report. */}
      <motion.div variants={rise}>
        <DuplicateListings onChanged={() => {
          loadListings({ quiet: true });
          refreshInsights();
        }} />
      </motion.div>

      {/* Suggested actions — the recommendation engine's picks, one collapsed
          group per category (expand for the per-listing rows).

          No control in the header. It used to carry the press that finishes
          the work, which was the right place for it while that press spanned
          two groups nothing else could reach — and the wrong place the
          moment those two became one row with a button of its own. Two
          buttons doing the same thing, one of them naming a number the group
          under it also named, is how a seller comes to distrust both. */}
      {insights.length > 0 && (
        <motion.div variants={rise}>
          <SectionHeader icon={Lightbulb} title="Suggested actions" />
          <Card className="p-0 divide-y divide-line overflow-hidden">
            {(() => {
              // Group by type, preserving arrival order: the API sorts by
              // priority desc, so groups order by their strongest rec. The
              // two halves of finishing a listing's details answer to one
              // key (see DETAILS), which is what makes them one row — and
              // they keep the place of whichever of them ranked highest.
              const keyOf = (type) =>
                (DETAILS_TYPES.includes(type) ? DETAILS : type);
              const groups = [];
              const byType = {};
              for (const rec of insights) {
                const key = keyOf(rec.type);
                if (!byType[key]) {
                  byType[key] = { type: key, recs: [], total: 0 };
                  groups.push(byType[key]);
                }
                byType[key].recs.push(rec);
              }
              // The server's count, straight through. It used to have this
              // browser's hidden rows netted off it, which is what a count
              // has to do while suggestions can be hidden — and is exactly
              // the arithmetic that made the badge hard to trust. Nothing
              // hides now, so the number on screen is the server's answer to
              // "how much is left", with nothing done to it here.
              for (const group of groups) {
                // The merged group's count is both halves added up — the
                // server counts per type and has no idea they render as one,
                // and a badge that showed only one half would be the same
                // undercount the per-type slice used to cause.
                const counted = group.type === DETAILS
                  ? DETAILS_TYPES.reduce((n, t) => n + (groupTotals[t] || 0), 0)
                  : (groupTotals[group.type] || 0);
                group.total = Math.max(counted, group.recs.length);
              }
              return groups.map((g) => (
                <RecGroup key={g.type} group={g} cap={bulkCaps[g.type]}
                  openListing={openListing} lowerAll={lowerAll}
                  finishEverything={finishEverything}
                  busy={bulkBusy === g.type}
                  progress={bulkProgress?.type === g.type ? bulkProgress : null} />
              ));
            })()}
          </Card>
        </motion.div>
      )}

      {/* Recent listings */}
      <motion.div variants={rise}>
        <SectionHeader
          icon={Tags}
          title="Recent listings"
          // Where the strip's contents actually live. All is the whole store,
          // the archive included, so a seller whose every listing has sold
          // lands on their sales there rather than on "No listings yet".
          action={items.length > 0 && (
            <Button variant="ghost" size="sm"
              onClick={() => openListings("all")}>
              View all <ArrowRight aria-hidden />
            </Button>
          )}
        />
        {listingsState.loading && !listingsState.loaded ? (
          <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-4">
            {[0, 1, 2, 3].map((i) => <ListingCardSkeleton key={i} />)}
          </div>
        ) : recent.length > 0 ? (
          <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-4">
            {recent.map((item) => (
              <div key={item.id} className="flex flex-col">
                <ListingCard className="h-full" item={item} onOpen={openListing} onDelete={askDelete}
                  metrics={metricsById[item.id]}
                  /* Rotate a draft's photo from here too — the same control
                     the drafts strip and the listings manager carry. */
                  onRotate={isDraft(item) ? rotateListingPhoto : undefined} />
                {/* The category, on the face of the card and one tap from
                    being fixed — the same control the drafts strip and the
                    bulk queue carry. A wrong category is the AI misfire that
                    costs most once it's published (and it decides which
                    conditions eBay will even accept), so it is not something
                    to find only after opening the full editor. */}
                {isDraft(item) && <DraftCategoryEdit item={item} className="mt-1.5" />}
                {/* And how it sells — Buy It Now, auction, or both. Drafts
                    only: eBay does not let a live listing change format. */}
                {isDraft(item) && <DraftFormatEdit item={item} className="mt-1.5" />}
              </div>
            ))}
          </div>
        ) : storeView.kind === "unavailable" ? (
          // Not the empty state: a read that failed is not evidence that the
          // seller has no listings, and this panel is the first thing they see.
          <Card>
            <p className="text-sm text-ink flex gap-2">
              <AlertTriangle size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
              <span>{storeView.message}</span>
            </p>
            <Button variant="soft" size="sm" className="mt-3"
              onClick={() => loadListings()}>
              Try again
            </Button>
          </Card>
        ) : items.length > 0 ? (
          // A store whose every listing has sold. Same rule as the failed
          // read above: "No listings yet", with a button to create a first
          // one, is the wrong thing to say to a seller who sold the lot.
          // Point them at the archive their listings actually went to.
          <Card className="p-0">
            <EmptyState
              illustration={ListingsIllustration}
              title="Everything's sold"
              message={"Nothing is waiting on you right now — every listing you "
                + "have is a finished sale, filed under Sold."}
              action={
                <div className="flex flex-wrap gap-2 justify-center">
                  <Button variant="primary" size="lg" onClick={startNew}>
                    <PlusCircle aria-hidden /> Create Listing
                  </Button>
                  <Button variant="soft" size="lg" onClick={() => openListings("inactive")}>
                    View sales <ArrowRight aria-hidden />
                  </Button>
                </div>
              }
            />
          </Card>
        ) : (
          <Card className="p-0">
            <EmptyState
              illustration={ListingsIllustration}
              title="No listings yet"
              message={user
                ? "Let's create your first listing — snap a few photos and the AI writes the rest."
                : "Log in to keep your listings, or jump straight in and create one."}
              action={
                <Button variant="primary" size="lg" onClick={startNew}>
                  <PlusCircle aria-hidden /> Create Listing
                </Button>
              }
            />
          </Card>
        )}
      </motion.div>
    </motion.div>
  );
}

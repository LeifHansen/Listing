import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Camera, Upload, PlusCircle, Store, ArrowRight, Rocket, FileText,
  Tags, Coins, Lightbulb, Megaphone, TrendingDown, RotateCcw,
  ListChecks, Loader2, RefreshCw, CheckCircle2, Eye, Heart, BarChart3,
  ChevronDown, DollarSign, AlertTriangle, Sparkles, X, Undo2,
} from "lucide-react";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { api, pollJob, postJson } from "@/lib/api";
import { readLocal, writeLocal } from "@/lib/localPrefs";
import { dismiss as dismissRec, readDismissed, restoreAll,
         withoutDismissed } from "@/lib/dismissedRecs";
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
import { isDraft, listingsView, recentListings, storeTotal }
  from "@/lib/listingsView";
import { DraftCategoryEdit } from "@/views/listing/CategoryQuickPick";
import { storeMirrorView } from "@/lib/storeMirror";

// The signed-out / no-suggestions list. A shared frozen constant so clearing
// it during render is a no-op state write when it is already empty, instead of
// a fresh [] that re-renders the whole dashboard. Never mutated.
const NO_INSIGHTS = Object.freeze([]);

// Icon + tone for each recommendation type from /api/insights.
const REC_ICON = {
  promote: Megaphone, lower_price: TrendingDown,
  finish: PlusCircle, relist: RotateCcw, photos: Camera, specifics: ListChecks,
};
const REC_TONE = {
  promote: "bg-blue-soft text-blue", lower_price: "bg-yellow-soft text-warning",
  finish: "bg-blue-soft text-blue",
  relist: "bg-red-soft text-error", photos: "bg-blue-soft text-blue",
  specifics: "bg-yellow-soft text-warning",
};
// Category headings for the grouped view — the per-rec `label` is an
// imperative for one listing ("Lower the price"); groups need the noun form.
const REC_GROUP_LABEL = {
  promote: "Promote listings",
  lower_price: "Lower prices",
  finish: "Finish & list",
  relist: "Relist ended items",
  photos: "Add more photos",
  specifics: "Fill in details",
};

// One suggestion row. Every row carries a dismiss control, because this list
// is rebuilt from scratch on every load: advice the seller has considered and
// decided against otherwise comes back for good, and a to-do list that will
// not shrink stops being read. See lib/dismissedRecs — and the "Restore
// dismissed" control on the section header, which is what keeps a mis-tapped
// X from being a one-way door.
function RecRow({ rec, promoting, promoteOne, openListing, onDismiss }) {
  const Icon = REC_ICON[rec.type] || Lightbulb;
  const isPromote = rec.type === "promote";
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
      {isPromote ? (
        <Button variant="soft" size="sm" className="shrink-0"
          loading={promoting === rec.listing_id} disabled={!!promoting}
          onClick={() => promoteOne(rec)}>
          {rec.rate ? `Promote ${rec.rate}%` : "Promote"}
        </Button>
      ) : (
        <Button variant="soft" size="sm" className="shrink-0"
          onClick={() => openListing(rec.listing_id)}>
          {rec.label} <ArrowRight aria-hidden />
        </Button>
      )}
      <Button variant="ghost" size="iconSm" className="shrink-0 -mr-1"
        aria-label={`Dismiss: ${rec.label} — ${rec.listing_title}`}
        title="Dismiss this suggestion"
        onClick={() => onDismiss(rec)}>
        <X aria-hidden />
      </Button>
    </div>
  );
}

// The group-level verbs. A suggestion category earns an entry here when the
// same edit makes sense across every listing in it — repeating one edit a dozen
// times by hand is the whole problem. `amount` marks the ones that need a
// number first (lower prices by HOW much); the rest fire on click. `shared`
// marks the one whose spinner is the per-listing `promoting` latch, because
// its rows fire the very same action.
//
// Photos, finish and relist are deliberately absent: photos need a human
// holding the item, and the last two create listings, which is not something
// to hand a single button.
const BULK_ACTIONS = {
  promote: {
    verb: "Promote all",
    icon: Megaphone,
    shared: true,
    run: (ctx) => ctx.promoteAll((ctx.group?.recs || []).length),
  },
  // "Fill in details" used to be a prompt to go and do it: open each listing,
  // wait for the AI to read its photos, save, repeat. It is the same edit
  // every time and the AI already knows how to make it, so it is a button —
  // one pass over the whole group, filling eBay's recommended item specifics
  // on each listing from its own photos and pushing them to the live listing.
  specifics: {
    verb: "Enrich all",
    icon: Sparkles,
    run: (ctx) => ctx.enrichAll(ctx.group),
  },
  lower_price: {
    verb: "Lower all…",
    icon: TrendingDown,
    amount: {
      unit: "%", initial: 10, min: 1, max: 75, step: 1,
      label: "Lower every price in this group by",
      submit: (n, value) => `Lower ${n} price${n === 1 ? "" : "s"} by ${value}%`,
      note: "New prices go straight to eBay. Anything that has sold or ended is skipped.",
    },
    run: (ctx, value) => ctx.lowerAll(ctx.group, value),
  },
};

// "Lower every price in this group by [ 10 ]%" — the amount an action needs
// before it can run, with its own submit. Rendered in normal flow under the
// group header rather than as a floating panel: the suggestions Card clips
// overflow, so anything absolutely positioned inside it gets cut off.
function BulkAmountPanel({ amount, count, busy, onSubmit, onCancel }) {
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
        {!valid && (
          <p className="mt-1 text-[12px] font-semibold text-error">
            Enter a number between {min} and {max}.
          </p>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button variant="primary" size="sm" loading={busy}
            disabled={busy || !valid} onClick={apply}>
            {submit(count, value)}
          </Button>
          <Button variant="ghost" size="sm" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </div>
    </motion.div>
  );
}

// One suggestion category: a collapsed header (icon, label, count) that
// expands to the full row list. Collapsed by default — eight "Lower the
// price" rows read as clutter; one "Lower prices · 8" reads as a to-do.
function RecGroup({ group, promoting, promoteAll, promoteOne, openListing,
                    lowerAll, enrichAll, onDismiss, busy, progress }) {
  const [open, setOpen] = useState(false);
  const [amountOpen, setAmountOpen] = useState(false);
  const Icon = REC_ICON[group.type] || Lightbulb;
  const action = BULK_ACTIONS[group.type];
  // Promote's spinner is the shared `promoting` latch, because its rows fire
  // the same action; every other group gets its own. Keyed off the action
  // rather than off "does it take an amount" — that read left a group whose
  // action needs no number spinning on the promote latch, so "Enrich all"
  // would have gone busy because a promote was running elsewhere.
  const actionBusy = action?.shared ? !!promoting : busy;
  const ActionIcon = action?.icon;
  return (
    <div>
      <div className="flex items-center gap-2 pr-4">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="flex-1 min-w-0 flex items-center gap-3.5 p-4 text-left cursor-pointer"
        >
          <span className={cn(
            "grid place-items-center size-10 rounded-[13px] shrink-0",
            REC_TONE[group.type] || "bg-blue-soft text-blue",
          )}>
            <Icon size={19} strokeWidth={2} aria-hidden />
          </span>
          <span className="font-semibold text-sm text-ink truncate">
            {REC_GROUP_LABEL[group.type] || group.recs[0].label}
          </span>
          <span className="grid place-items-center font-display tabular-nums text-[11px] font-bold rounded-full bg-bg-sunken px-1.5 min-w-5 h-5 text-ink-secondary">
            {group.recs.length}
          </span>
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
            loading={actionBusy} disabled={actionBusy}
            aria-expanded={action.amount ? amountOpen : undefined}
            onClick={() => (action.amount
              ? setAmountOpen((o) => !o)
              : action.run({ group, promoteAll, lowerAll, enrichAll }))}>
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
          </span>
        </p>
      )}
      <AnimatePresence initial={false}>
        {action?.amount && amountOpen && (
          <BulkAmountPanel
            amount={action.amount} count={group.recs.length} busy={busy}
            onCancel={() => setAmountOpen(false)}
            onSubmit={(value) => {
              setAmountOpen(false);
              action.run({ group, promoteAll, lowerAll, enrichAll }, value);
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
            <div className="divide-y divide-line border-t border-line">
              {group.recs.map((rec) => (
                <RecRow key={`${rec.listing_id}-${rec.type}`} rec={rec}
                  promoting={promoting} promoteOne={promoteOne}
                  openListing={openListing} onDismiss={onDismiss} />
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
function soldSub(sales, soldEnded) {
  const ended = soldEnded.filter((i) => i.status === "ended").length;
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
  const { user, openAuth, listingsState, loadListings, startNew, openListing, setView, openListings, session, deleteListing, metricsById, metricsStatus, ebay, tokens, loadTokens } = useApp();
  const { confirm, toast } = useToast();
  const items = listingsState.items;
  const storeView = listingsView({
    ...listingsState, user, count: items.length,
  });

  // "What to do next" — ranked actions across the user's listings.
  const [insights, setInsights] = useState(NO_INSIGHTS);
  const [promoting, setPromoting] = useState(null); // listing id, or "all"
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
  // account's listings (with live Promote / Lower-price buttons) on a
  // signed-out dashboard. Level-triggering costs an identity check and
  // converges: once `insights` is the shared empty, the write is skipped.
  if (!user && insights !== NO_INSIGHTS) setInsights(NO_INSIGHTS);
  const refreshInsights = useCallback(() => {
    if (!user) return;
    api("/api/insights")
      .then((r) => setInsights(r.recommendations || NO_INSIGHTS))
      .catch(() => {});
  }, [user]);
  useEffect(() => { refreshInsights(); }, [refreshInsights, items.length]);

  // The suggestions this seller has waved away. Read once, from this browser
  // (see lib/dismissedRecs); the API has no idea and rebuilds the full list
  // every time, so the filtering happens here.
  const [dismissed, setDismissed] = useState(readDismissed);
  const visibleInsights = withoutDismissed(insights, dismissed);
  const hiddenCount = insights.length - visibleInsights.length;
  const dismissOne = (rec) => setDismissed((d) => dismissRec(d, rec));
  const restoreDismissed = () => setDismissed(restoreAll());

  const afterPromote = (res) => {
    if (res.needs_reconnect) {
      toast("Reconnect eBay in Settings to grant ad permissions, then try again.", { kind: "warning" });
    }
    refreshInsights();
    loadListings({ quiet: true });
  };
  const promoteOne = async (rec) => {
    setPromoting(rec.listing_id);
    try {
      const res = await postJson("/api/ebay/promote",
        { listing_id: rec.listing_id, ad_rate_percent: rec.rate || 0 });
      if (res.ok) toast(`Promoting at ${res.ad_rate}% — you only pay if it sells through the ad.`, { kind: "success" });
      else if (!res.needs_reconnect) toast(res.message || "Couldn't start the promotion.", { kind: "error" });
      afterPromote(res);
    } catch (e) {
      toast(`Couldn't promote: ${e.message}`, { kind: "error" });
    } finally { setPromoting(null); }
  };
  const promoteAll = async (count = 0) => {
    // Promoting costs money on every sale it touches, and this button reaches
    // EVERY live listing at once — the one action in the app that spends
    // across the whole store from a single tap. Say what it will do first.
    if (!(await confirm({
      title: count ? `Promote ${count} listings?` : "Promote every live listing?",
      message: "Each gets eBay's recommended ad rate. Promoted Listings is "
        + "pay-per-sale — you're charged that percentage only when a listing "
        + "sells through its ad, but it applies to every listing this touches.",
      confirmLabel: "Promote them",
    }))) return;
    setPromoting("all");
    try {
      const res = await postJson("/api/ebay/promote-all", {});
      if (res.promoted) toast(`Promoting ${res.promoted} listing${res.promoted === 1 ? "" : "s"} at eBay's recommended rate.`, { kind: "success" });
      else if (!res.needs_reconnect) toast("No live listings to promote.", { kind: "info" });
      afterPromote(res);
    } catch (e) {
      toast(`Couldn't promote all: ${e.message}`, { kind: "error" });
    } finally { setPromoting(null); }
  };

  // Bulk price drop across one suggestion group. Reports per-listing outcomes
  // rather than a bare success: over a dozen listings some will have sold or
  // ended since the suggestion was computed, and "lowered 11, skipped 1" is
  // the honest answer.
  const [bulkBusy, setBulkBusy] = useState(null); // group type, or null
  const lowerAll = async (group, percent) => {
    const ids = group.recs.map((r) => r.listing_id);
    setBulkBusy(group.type);
    try {
      const res = await postJson("/api/ebay/lower-prices",
        { percent, listing_ids: ids });
      const parts = [];
      if (res.changed) parts.push(`Lowered ${res.changed} price${res.changed === 1 ? "" : "s"} by ${percent}%`);
      if (res.skipped) parts.push(`${res.skipped} skipped`);
      if (res.failed) parts.push(`${res.failed} failed`);
      // The server caps one run so the request can't outlive the gateway.
      if (res.deferred) parts.push(`${res.deferred} left — run it again to finish`);
      toast(parts.join(" · ") || "Nothing to change.", {
        kind: res.changed ? "success" : res.failed ? "error" : "info",
      });
      refreshInsights();
      loadListings({ quiet: true });
    } catch (e) {
      toast(`Couldn't lower prices: ${e.message}`, { kind: "error" });
    } finally { setBulkBusy(null); }
  };

  // "Enrich all" — the whole "Fill in details" group filled in at once.
  //
  // This is what the suggestion used to ask the seller to do by hand: open a
  // listing, wait for the AI to read its photos, let it fill eBay's
  // recommended item specifics, save, push, repeat. The edit is the same one
  // every time and nothing about it needs a human, so the group does it.
  //
  // It runs as a background JOB rather than one long request: a vision pass
  // per listing over a dozen listings is minutes of work, which no browser
  // (or the proxy in front of the server) will hold a connection open for.
  // `bulkProgress` is what the job reports as it goes, rendered on the group.
  const [bulkProgress, setBulkProgress] = useState(null);
  const enrichAll = async (group) => {
    const ids = group.recs.map((r) => r.listing_id);
    const n = ids.length;
    // Every listing this touches spends AI credits, and this button reaches a
    // whole group from one tap. Say what it will do — and what it will cost —
    // before it does it, the same way promoting the store does.
    const cost = tokens.enabled && tokens.costs?.specifics
      ? ` It uses ${tokens.costs.specifics * n} AI tokens (${tokens.costs.specifics} per listing); you have ${tokens.total}.`
      : "";
    if (!(await confirm({
      title: `Fill in details on ${n} listing${n === 1 ? "" : "s"}?`,
      message: "The AI reads each listing's own photos and fills in eBay's "
        + "recommended item specifics — the fields buyers filter by — then "
        + "pushes them to the live listing. Anything you've already written "
        + `is left exactly as it is.${cost}`,
      confirmLabel: "Fill them in",
    }))) return;
    setBulkBusy(group.type);
    setBulkProgress({ type: group.type, done: 0, total: n, title: "" });
    try {
      const start = await postJson("/api/listings/enrich", { listing_ids: ids });
      const res = await pollJob(start.job_id, {
        onUpdate: (j) => setBulkProgress({
          type: group.type, done: j.current || 0,
          total: j.total_items || n, title: j.current_title || "",
        }),
      });
      const parts = [];
      if (res.changed) {
        parts.push(`Filled in ${res.changed} listing${res.changed === 1 ? "" : "s"}`
          + (res.filled ? ` · ${res.filled} detail${res.filled === 1 ? "" : "s"} added` : ""));
      }
      // Skipped is the honest half: a listing with no category, no photos left
      // on the server, or nothing its photos could answer is not a failure and
      // is not done either.
      if (res.skipped) parts.push(`${res.skipped} need you`);
      if (res.failed) parts.push(`${res.failed} failed`);
      if (res.deferred) parts.push(`${res.deferred} left — run it again to finish`);
      if (res.stopped) parts.push(res.stopped);
      toast(parts.join(" · ") || "Nothing to fill in.", {
        kind: res.changed ? "success" : res.failed ? "error" : "info",
      });
      refreshInsights();
      loadListings({ quiet: true });
      loadTokens();
    } catch (e) {
      toast(`Couldn't fill these in: ${e.message}`, { kind: "error" });
    } finally { setBulkBusy(null); setBulkProgress(null); }
  };

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
  const soldEnded = items.filter((i) => i.status === "sold" || i.status === "ended");
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

  // The four newest listings the seller can still act on. Sold ones are left
  // out, the same way the Sell screen's tabs leave them out: a sale is
  // archived under Inactive there, but this strip sorted the whole store by
  // `updated_at` and a sale is the last thing that touches a row — so the
  // item that had just left the Sell screen went straight to the top of the
  // dashboard, took a quarter of the strip, and pushed a live listing off it.
  const recent = recentListings(items);

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
                         soldSub(sales, soldEnded))}
          action={<SoldRangePicker value={soldRangeId} onChange={pickSoldRange} />}
          onClick={() => openListings("inactive")} />
        <StatCard icon={Rocket} tone="red" label="Listed today"
          {...storeTotal(storeView.kind, todays.length,
                         todays.length ? "keep the streak going"
                                       : "photos in, listing out — ~30s")}
          onClick={startNew} />
      </motion.div>

      {/* Traffic — real eBay numbers for the live listings (Sell Analytics
          views/impressions over 30 days + watchers), with the top performer.
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
                <BarChart3 size={15} className="text-blue" aria-hidden /> Traffic · 30 days
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
          group per category (expand for the per-listing rows), minus whatever
          the seller has dismissed.

          The section is gated on the WHOLE list rather than the visible one,
          so dismissing the last row leaves the "Restore dismissed" control on
          screen instead of taking it away with the thing it undoes. */}
      {insights.length > 0 && (
        <motion.div variants={rise}>
          <SectionHeader icon={Lightbulb} title="Suggested actions"
            action={hiddenCount > 0 && (
              <Button variant="ghost" size="sm" onClick={restoreDismissed}>
                <Undo2 aria-hidden /> Restore {hiddenCount} dismissed
              </Button>
            )} />
          <Card className="p-0 divide-y divide-line overflow-hidden">
            {visibleInsights.length === 0 ? (
              <p className="p-4 text-[13px] text-ink-secondary">
                Nothing left here — every suggestion is dismissed.
              </p>
            ) : (() => {
              // Group by type, preserving arrival order: the API sorts by
              // priority desc, so groups order by their strongest rec.
              const groups = [];
              const byType = {};
              for (const rec of visibleInsights) {
                if (!byType[rec.type]) {
                  byType[rec.type] = { type: rec.type, recs: [] };
                  groups.push(byType[rec.type]);
                }
                byType[rec.type].recs.push(rec);
              }
              return groups.map((g) => (
                <RecGroup key={g.type} group={g} promoting={promoting}
                  promoteAll={promoteAll} promoteOne={promoteOne}
                  openListing={openListing} lowerAll={lowerAll}
                  enrichAll={enrichAll} onDismiss={dismissOne}
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
          // Where the strip's contents actually live. "All" hides sold, so a
          // seller whose whole store has sold would otherwise be sent from an
          // empty strip to an empty tab reading "No listings yet".
          action={items.length > 0 && (
            <Button variant="ghost" size="sm"
              onClick={() => openListings(recent.length > 0 ? "all" : "inactive")}>
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
                  metrics={metricsById[item.id]} />
                {/* The category, on the face of the card and one tap from
                    being fixed — the same control the drafts strip and the
                    bulk queue carry. A wrong category is the AI misfire that
                    costs most once it's published (and it decides which
                    conditions eBay will even accept), so it is not something
                    to find only after opening the full editor. */}
                {isDraft(item) && <DraftCategoryEdit item={item} className="mt-1.5" />}
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
                + "have is a finished sale, filed under Inactive."}
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

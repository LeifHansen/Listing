import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Camera, Upload, PlusCircle, Store, ArrowRight, Rocket, FileText,
  Tags, Coins, Lightbulb, Megaphone, TrendingDown, RotateCcw,
  ListChecks, Loader2, RefreshCw, CheckCircle2, Eye, Heart, BarChart3,
  ChevronDown, DollarSign,
} from "lucide-react";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { api, postJson } from "@/lib/api";
import { Card, SectionHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { StatCard } from "@/components/ui/StatCard";
import { ListingCard } from "@/components/ListingCard";
import { DuplicateListings } from "@/components/DuplicateListings";
import { ListingCardSkeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ListingsIllustration, WelcomeIllustration } from "@/components/ui/illustrations";
import { cn, formatMoney } from "@/lib/utils";
import { DEFAULT_SOLD_RANGE, SOLD_RANGES, salesSummary } from "@/lib/sales";

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

// One suggestion row — the pre-grouping list item, unchanged.
function RecRow({ rec, promoting, promoteOne, openListing }) {
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
    </div>
  );
}

// The group-level verbs. A suggestion category earns an entry here when the
// same edit makes sense across every listing in it — repeating one edit a dozen
// times by hand is the whole problem. `amount` marks the ones that need a
// number first (lower prices by HOW much); the rest fire on click.
//
// Photos, specifics, finish and relist are deliberately absent: the first two
// need a human looking at each item, and the last two create listings, which is
// not something to hand a single button.
const BULK_ACTIONS = {
  promote: {
    verb: "Promote all",
    icon: Megaphone,
    run: (ctx) => ctx.promoteAll((ctx.group?.recs || []).length),
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
                    lowerAll, busy }) {
  const [open, setOpen] = useState(false);
  const [amountOpen, setAmountOpen] = useState(false);
  const Icon = REC_ICON[group.type] || Lightbulb;
  const action = BULK_ACTIONS[group.type];
  // Promote's spinner is the shared `promoting` latch (its rows share it);
  // parameterized actions get the per-group one.
  const actionBusy = action?.amount ? busy : !!promoting;
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
              : action.run({ group, promoteAll, lowerAll }))}>
            <ActionIcon aria-hidden /> {action.verb}
          </Button>
        )}
      </div>
      <AnimatePresence initial={false}>
        {action?.amount && amountOpen && (
          <BulkAmountPanel
            amount={action.amount} count={group.recs.length} busy={busy}
            onCancel={() => setAmountOpen(false)}
            onSubmit={(value) => {
              setAmountOpen(false);
              action.run({ group, promoteAll, lowerAll }, value);
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
  if (!user) return null;
  if (!ebay.connected) {
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
  if (storeSync.syncing) {
    // A store of any size takes minutes (one eBay call per listing), so the
    // line carries the count the background job reports — a bare spinner with
    // no end in sight reads as broken.
    const p = storeSync.progress;
    const detail = p && p.total
      ? (p.phase === "saving"
        ? ` saving ${Math.min(p.done, p.total)} of ${p.total}`
        : ` ${Math.min(p.done, p.total)} of ${p.total} listings`)
      : "";
    return (
      <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-ink-secondary">
        <Loader2 size={14} className="animate-spin" aria-hidden />
        {` Syncing your eBay store…${detail}`}
      </span>
    );
  }
  if (storeSync.error) {
    return (
      <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-warning">
        <RefreshCw size={14} aria-hidden /> Store sync hit a snag — retry from Listings
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-ink-secondary"
      title="Everything below reflects your actual eBay store — created here or not.">
      <CheckCircle2 size={14} className="text-success" aria-hidden /> Live mirror of your eBay store
    </span>
  );
}

// The sold tile's window. Remembered across visits — a seller who thinks in
// months shouldn't have to re-pick every morning. Rendered as a plain select:
// it lives in the tile's corner as a sibling of the tile button (see
// StatCard's `action`), so it has to be a real control, not a nested one.
const SOLD_RANGE_KEY = "quickflip-sold-range";

function readSoldRange() {
  try {
    const saved = localStorage.getItem(SOLD_RANGE_KEY);
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
  if (sales.profit != null) {
    const sign = sales.profit >= 0 ? "+" : "−";
    parts.push(`${sign}${formatMoney(Math.abs(sales.profit))} profit`);
  }
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
  const { user, openAuth, listingsState, loadListings, startNew, openListing, setView, openListings, session, deleteListing, metricsById, metricsStatus, ebay } = useApp();
  const { confirm, toast } = useToast();
  const items = listingsState.items;

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
    try { localStorage.setItem(SOLD_RANGE_KEY, id); } catch (e) { /* private mode */ }
  };
  const revenue = live.reduce((sum, i) => sum + (Number(i.listing?.price) || 0), 0);
  const watcherTotal = live.reduce((sum, i) => {
    const m = metricsById[i.id];
    const w = m?.watchers ?? i.listing?.watch_count ?? 0;
    return sum + (Number(w) || 0);
  }, 0);

  // An in-memory session resumes directly — but NOT once it's gone live (or
  // otherwise left the draft stage): there's nothing to "continue" on a
  // published listing, so fall through to the newest actual draft instead.
  const sessionItem = session ? items.find((i) => i.id === session.sessionId) : null;
  const sessionDone = ["published", "live", "sold", "ended"].includes(
    session?.status || sessionItem?.status);
  const lastOpen = (session && !sessionDone)
    ? { title: session.listing?.title, go: () => setView("new") }
    : (drafts[0] && {
        title: drafts[0].listing?.title || drafts[0].title,
        go: () => openListing(drafts[0].id),
      });

  const recent = [...items]
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""))
    .slice(0, 4);

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
        <StatCard icon={Coins} tone="green" label="Active on eBay"
          value={live.length}
          sub={revenue > 0
            ? `${formatMoney(revenue)} listed${watcherTotal ? ` · ${watcherTotal} watcher${watcherTotal === 1 ? "" : "s"}` : ""}`
            : "everything currently live"}
          onClick={() => openListings("active")} />
        <StatCard icon={FileText} tone="yellow" label="Drafts in progress"
          value={drafts.length}
          sub={inventory.length
            ? `+ ${inventory.length} unlisted find${inventory.length === 1 ? "" : "s"} from Shop Mode`
            : "open one to finish & publish"}
          onClick={() => openListings("drafts")} />
        <StatCard icon={DollarSign} tone="blue" label="Sold"
          value={formatMoney(sales.total) || "$0.00"}
          sub={soldSub(sales, soldEnded)}
          action={<SoldRangePicker value={soldRangeId} onChange={pickSoldRange} />}
          onClick={() => openListings("inactive")} />
        <StatCard icon={Rocket} tone="red" label="Listed today"
          value={todays.length}
          sub={todays.length ? "keep the streak going" : "photos in, listing out — ~30s"}
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
          group per category (expand for the per-listing rows). */}
      {insights.length > 0 && (
        <motion.div variants={rise}>
          <SectionHeader icon={Lightbulb} title="Suggested actions" />
          <Card className="p-0 divide-y divide-line overflow-hidden">
            {(() => {
              // Group by type, preserving arrival order: the API sorts by
              // priority desc, so groups order by their strongest rec.
              const groups = [];
              const byType = {};
              for (const rec of insights) {
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
                  busy={bulkBusy === g.type} />
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
          action={items.length > 0 && (
            <Button variant="ghost" size="sm" onClick={() => openListings("all")}>
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
              <ListingCard key={item.id} className="h-full" item={item} onOpen={openListing} onDelete={askDelete}
                metrics={metricsById[item.id]} />
            ))}
          </div>
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

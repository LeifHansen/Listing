import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Camera, Upload, PlusCircle, Store, ArrowRight, Rocket, FileText,
  Tags, Coins, Lightbulb, Megaphone, TrendingDown, Tag, RotateCcw,
  ListChecks, Loader2, RefreshCw, CheckCircle2, Eye, Heart, BarChart3,
  ChevronDown,
} from "lucide-react";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { api, postJson } from "@/lib/api";
import { Card, SectionHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { StatCard } from "@/components/ui/StatCard";
import { ListingCard } from "@/components/ListingCard";
import { ListingCardSkeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { BoxIllustration, RobotIllustration } from "@/components/ui/illustrations";
import { cn, formatMoney } from "@/lib/utils";

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
    run: (ctx) => ctx.promoteAll(),
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
          <span className="grid place-items-center tabular-nums text-[11px] font-bold rounded-full bg-bg-sunken px-1.5 min-w-5 h-5 text-ink-secondary">
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
    return (
      <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-ink-secondary">
        <Loader2 size={14} className="animate-spin" aria-hidden /> Syncing your eBay store…
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

export function Dashboard() {
  const { user, openAuth, listingsState, loadListings, startNew, openListing, setView, openListings, session, deleteListing, metricsById } = useApp();
  const { confirm, toast } = useToast();
  const items = listingsState.items;

  // "What to do next" — ranked actions across the user's listings.
  const [insights, setInsights] = useState([]);
  const [promoting, setPromoting] = useState(null); // listing id, or "all"
  const refreshInsights = useCallback(() => {
    if (!user) { setInsights([]); return; }
    api("/api/insights")
      .then((r) => setInsights(r.recommendations || []))
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
  const promoteAll = async () => {
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
              <h1 className="text-2xl sm:text-[28px] font-bold tracking-tight text-ink">
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
              <RobotIllustration />
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
        <StatCard icon={Tag} tone="blue" label="Sold & ended"
          value={soldEnded.length}
          sub="relist ended items in one tap"
          onClick={() => openListings(soldEnded.some((i) => i.status === "sold") ? "sold" : "inactive")} />
        <StatCard icon={Rocket} tone="red" label="Listed today"
          value={todays.length}
          sub={todays.length ? "keep the streak going" : "photos in, listing out — ~30s"}
          onClick={startNew} />
      </motion.div>

      {/* Traffic — real eBay numbers for the live listings (Sell Analytics
          views/impressions over 30 days + watchers), with the top performer. */}
      {(() => {
        const withMetrics = live.filter((i) => metricsById[i.id]);
        if (!withMetrics.length) return null;
        const views = withMetrics.reduce((s, i) => s + (metricsById[i.id].views || 0), 0);
        const impressions = withMetrics.reduce((s, i) => s + (metricsById[i.id].impressions || 0), 0);
        const top = [...withMetrics].sort((a, b) =>
          (metricsById[b.id].views || 0) - (metricsById[a.id].views || 0))[0];
        const topViews = metricsById[top.id].views || 0;
        return (
          <motion.div variants={rise}>
            <Card className="py-3.5 px-5 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[13px] text-ink-secondary">
              <span className="inline-flex items-center gap-1.5 font-semibold text-ink">
                <BarChart3 size={15} className="text-blue" aria-hidden /> Traffic · 30 days
              </span>
              <span className="inline-flex items-center gap-1 tabular-nums">
                <Eye size={14} aria-hidden /> {views.toLocaleString()} views
              </span>
              {impressions > 0 && (
                <span className="tabular-nums">{impressions.toLocaleString()} search impressions</span>
              )}
              <span className="inline-flex items-center gap-1 tabular-nums">
                <Heart size={14} aria-hidden /> {watcherTotal.toLocaleString()} watchers
              </span>
              {topViews > 0 && (
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
            </Card>
          </motion.div>
        );
      })()}

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
              <ListingCard key={item.id} item={item} onOpen={openListing} onDelete={askDelete}
                metrics={metricsById[item.id]} />
            ))}
          </div>
        ) : (
          <Card className="p-0">
            <EmptyState
              illustration={BoxIllustration}
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

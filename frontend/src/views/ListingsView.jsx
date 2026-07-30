import { useState } from "react";
import { motion } from "framer-motion";
import {
  PlusCircle, Store, LogIn, RefreshCw, CheckSquare, Trash2, X,
} from "lucide-react";
import { pollJob, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { OriginChip, originOf } from "@/components/ui/badges";
import { ListingCard } from "@/components/ListingCard";
import { ListingCardSkeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  BoxIllustration, TagIllustration, ClipboardIllustration,
} from "@/components/ui/illustrations";
import { cn } from "@/lib/utils";

/* The unified listings pipeline: ONE view of the seller's whole store, cut by
   lifecycle tab — the way sellers actually think about their pile. Replaces
   the old Inventory / Drafts / Listings triplet. */

const TABS = [
  {
    id: "active", label: "Active", statuses: ["published", "live"],
    sub: "Everything currently live on eBay — created here or imported",
    empty: {
      illustration: TagIllustration, title: "Nothing live yet",
      message: "Publish a draft (or create a listing from photos) and it shows up here the moment it's live.",
      action: { label: "Create Listing", icon: PlusCircle, go: "new" },
    },
  },
  {
    id: "drafts", label: "Drafts", statuses: ["draft", "dry_run"],
    sub: "Works in progress — open one to finish and publish",
    selectable: true, draftTools: true,
    empty: {
      illustration: ClipboardIllustration, title: "No drafts",
      message: "Drafts save automatically while you build a listing, so you can pick up right where you left off.",
      action: { label: "Create Listing", icon: PlusCircle, go: "new" },
    },
  },
  {
    id: "finds", label: "Finds", statuses: ["unlisted"],
    sub: "Shop Mode finds waiting to become listings",
    empty: {
      illustration: BoxIllustration, title: "No unlisted finds",
      message: "Scan items in Shop Mode while you're out hunting — tap Buy and they land here to finish later.",
      action: { label: "Open Shop Mode", icon: Store, go: "shop" },
    },
  },
  {
    id: "inactive", label: "Inactive", statuses: ["ended"],
    sub: "Ended listings — no longer on eBay; open one to relist it fresh",
    empty: {
      illustration: TagIllustration, title: "No inactive listings",
      message: "Listings you end (the ⊘ button on an active card) and eBay listings that "
        + "end without selling both collect here, one tap from a fresh relist.",
    },
  },
  {
    id: "sold", label: "Sold", statuses: ["sold"],
    sub: "Sold on eBay — nice work",
    empty: {
      illustration: TagIllustration, title: "No sales recorded yet",
      message: "When something sells on eBay, the mirror moves it here automatically.",
    },
  },
  {
    id: "all", label: "All", statuses: null,
    sub: "A live mirror of your whole eBay store — every status at once",
    empty: {
      illustration: TagIllustration, title: "No listings yet",
      message: "Let's create your first listing — snap a few photos and the AI writes the rest.",
      action: { label: "Create Listing", icon: PlusCircle, go: "new" },
    },
  },
];

// Active listings older than this get the amber "stale" clock: relisting
// fresh mints a new item id and a search-placement boost.
const STALE_DAYS = 60;
const dayAge = (iso) => (iso ? (Date.now() - Date.parse(iso)) / 86400000 : 0);

const isDraft = (item) => item.status === "draft" || item.status === "dry_run";

export function ListingsView({ search = "", forceTab }) {
  const {
    listingsState, openListing, setView, startNew, user, openAuth, deleteListing,
    bulkDeleteListings, ebay, loadListings, metricsById,
    skippedDraftIds, toggleSkipDraft, storeSync, syncStore,
    listingsTab, setListingsTab,
  } = useApp();
  const { confirm, toast } = useToast();

  const tabId = forceTab || listingsTab || "active";
  const tab = TABS.find((t) => t.id === tabId) || TABS[0];
  const pick = (t) => { setListingsTab(t); setView("listings"); };

  const counts = Object.fromEntries(TABS.map((t) => [
    t.id,
    t.statuses
      ? listingsState.items.filter((i) => t.statuses.includes(i.status)).length
      : listingsState.items.length,
  ]));

  // Select mode (Drafts): tap cards to select, then delete them all at once.
  const [selecting, setSelecting] = useState(false);
  const [sel, setSel] = useState({});
  const selIds = Object.keys(sel).filter((id) => sel[id]);
  const exitSelect = () => { setSelecting(false); setSel({}); };
  const deleteSelected = async () => {
    if (!selIds.length) return;
    if (!(await confirm({
      title: `Delete ${selIds.length} draft${selIds.length === 1 ? "" : "s"}?`,
      message: "They'll be permanently removed, photos included. This can't be undone.",
      confirmLabel: "Delete all selected",
      danger: true,
    }))) return;
    if (await bulkDeleteListings(selIds)) exitSelect();
  };

  // Manual re-run of the store mirror (the mirror itself runs at app load).
  const importFromEbay = async () => {
    const r = await syncStore({ force: true });
    if (!r) return;
    if (r.error) {
      toast(`Couldn't sync with eBay: ${r.error}`, { kind: "error" });
      return;
    }
    const fresh = r.imported || 0;
    toast(
      fresh || r.updated
        ? `Synced ${r.found} eBay listing${r.found === 1 ? "" : "s"} — ${fresh} new, ${r.updated} updated.`
        : "Everything's already in sync with eBay.",
      { kind: "success" },
    );
    if (r.failed) {
      toast(`${r.failed} listing${r.failed === 1 ? "" : "s"} couldn't be read from eBay.`,
        { kind: "warning" });
    }
  };

  // Start over on a draft: throw away the drafted copy and re-run the AI over
  // the listing's own photos.
  const [startingOver, setStartingOver] = useState(null); // id, or null
  const startOver = async (item) => {
    const name = item.listing?.title || item.title || "this draft";
    if (!(await confirm({
      title: "Start this listing over?",
      message: `The AI will look at "${name}"'s photos again and rewrite the `
        + "title, description, and item specifics. Your photos are kept; any "
        + "edits you made to the text are replaced.",
      confirmLabel: "Start over",
    }))) return;
    setStartingOver(item.id);
    try {
      const { job_id } = await postJson(`/api/identify-async/${item.id}`, {});
      await pollJob(job_id);
      await loadListings({ quiet: true });
      toast("Rewritten from the photos.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't start over: ${e.message}`, { kind: "error" });
    } finally {
      setStartingOver(null);
    }
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

  // End a live listing straight from its card: it comes off eBay and moves to
  // the Inactive tab, relistable anytime — never a permanent delete.
  const [endingId, setEndingId] = useState(null);
  const askEnd = async (item) => {
    const name = item.listing?.title || item.title || "this listing";
    if (!(await confirm({
      title: "End this listing on eBay?",
      message: `"${name}" comes off eBay immediately and moves to Inactive — you can edit and relist it anytime.`,
      confirmLabel: "End listing",
      danger: true,
    }))) return;
    setEndingId(item.id);
    try {
      await postJson("/api/ebay/end-listing", { session_id: item.id });
      await loadListings({ quiet: true });
      toast("Listing ended — find it under Inactive.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't end the listing: ${e.message}`, { kind: "error" });
    } finally {
      setEndingId(null);
    }
  };

  const q = search.trim().toLowerCase();
  const items = listingsState.items
    .filter((i) => (tab.statuses ? tab.statuses.includes(i.status) : true))
    .filter((i) => !q
      || (i.listing?.title || i.title || "").toLowerCase().includes(q)
      || (i.listing?.brand || "").toLowerCase().includes(q)
      || (i.listing?.description || "").toLowerCase().includes(q))
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));

  const go = () => (tab.empty.action?.go === "new" ? startNew() : setView(tab.empty.action.go));

  let body;
  if (listingsState.loading && !listingsState.loaded) {
    body = (
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {[0, 1, 2, 3, 4, 5].map((i) => <ListingCardSkeleton key={i} />)}
      </div>
    );
  } else if (!listingsState.dbConfigured) {
    body = (
      <Card>
        <p className="text-sm text-ink-secondary">
          No database configured — set DATABASE_URL on the server to save listing history.
        </p>
      </Card>
    );
  } else if (!user) {
    body = (
      <Card className="p-0">
        <EmptyState
          illustration={tab.empty.illustration}
          title="Log in to see your listings"
          message="Your listings, drafts, and Shop Mode finds are saved to your account."
          action={
            <Button variant="primary" size="lg" onClick={() => openAuth()}>
              <LogIn aria-hidden /> Log in
            </Button>
          }
        />
      </Card>
    );
  } else if (items.length === 0) {
    body = (
      <Card className="p-0">
        <EmptyState
          illustration={tab.empty.illustration}
          title={q ? "No matches" : tab.empty.title}
          message={q ? `Nothing matches "${search}" here.` : tab.empty.message}
          action={!q && tab.empty.action && (
            <Button variant="primary" size="lg" onClick={go}>
              <tab.empty.action.icon aria-hidden /> {tab.empty.action.label}
            </Button>
          )}
        />
      </Card>
    );
  } else {
    body = (
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {items.map((item, i) => (
          <motion.div
            key={item.id}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, delay: Math.min(i * 0.03, 0.3) }}
          >
            <ListingCard item={item} onOpen={openListing} onDelete={askDelete}
              onEnd={(item.status === "published" || item.status === "live") ? askEnd : undefined}
              ending={endingId === item.id}
              onStartOver={tab.draftTools && isDraft(item) ? startOver : undefined}
              startingOver={startingOver === item.id}
              onSkip={tab.draftTools && isDraft(item) ? () => toggleSkipDraft(item.id) : undefined}
              skipped={skippedDraftIds.has(item.id)}
              stale={(item.status === "published" || item.status === "live")
                && dayAge(item.created_at) >= STALE_DAYS}
              metrics={metricsById[item.id]}
              selectable={selecting}
              selected={!!sel[item.id]}
              onSelect={() => setSel((s) => ({ ...s, [item.id]: !s[item.id] }))} />
          </motion.div>
        ))}
      </div>
    );
  }

  const hasItems = user && listingsState.dbConfigured && items.length > 0;
  const showLegend = (tabId === "active" || tabId === "all") && hasItems;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-ink">Listings</h1>
          <p className="text-sm text-ink-secondary mt-1">{tab.sub}</p>
        </div>
        <div className="flex items-center gap-2">
          {user && ebay.connected && (
            <Button variant="soft" onClick={importFromEbay} loading={storeSync.syncing}>
              <RefreshCw aria-hidden /> Sync with eBay
            </Button>
          )}
          {tab.selectable && hasItems && (selecting ? (
            <>
              <Button variant="danger" size="sm" onClick={deleteSelected}
                disabled={!selIds.length}>
                <Trash2 aria-hidden /> Delete selected ({selIds.length})
              </Button>
              <Button variant="ghost" size="sm"
                onClick={() => setSel(Object.fromEntries(items.map((i) => [i.id, true])))}>
                All
              </Button>
              <Button variant="ghost" size="sm" onClick={exitSelect}>
                <X aria-hidden /> Cancel
              </Button>
            </>
          ) : (
            <Button variant="ghost" size="sm" onClick={() => setSelecting(true)}>
              <CheckSquare aria-hidden /> Select
            </Button>
          ))}
        </div>
      </div>

      {/* The pipeline: one tab per lifecycle stage, with live counts. "Finds"
          only appears once Shop Mode has produced any. */}
      <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 pb-0.5">
        {TABS.filter((t) => t.id !== "finds" || counts.finds > 0).map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => { pick(t.id); exitSelect(); }}
            aria-pressed={tabId === t.id}
            className={cn(
              "shrink-0 inline-flex items-center gap-1.5 h-9 px-3.5 rounded-full text-[13px]",
              "font-semibold cursor-pointer transition-colors duration-150 border",
              tabId === t.id
                ? "bg-blue text-on-accent border-blue"
                : "bg-card text-ink-secondary border-line hover:text-ink hover:border-line-strong",
            )}
          >
            {t.label}
            <span className={cn(
              "tabular-nums text-[11px] font-bold rounded-full px-1.5 min-w-5 h-5 grid place-items-center",
              tabId === t.id ? "bg-white/20" : "bg-bg-sunken",
            )}>
              {counts[t.id]}
            </span>
          </button>
        ))}
      </div>

      {/* Origin legend: which badges appear in this grid and what each one is
          allowed to do — hover (or long-press) a chip for the full rules. */}
      {showLegend && (() => {
        const present = [...new Set(items.map(originOf))];
        return (
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 text-[12px] text-ink-faint -mt-1">
            {present.map((k) => <OriginChip key={k} kind={k} />)}
            <span>— hover a badge to see what it can and can’t do</span>
          </div>
        );
      })()}
      {body}
    </div>
  );
}

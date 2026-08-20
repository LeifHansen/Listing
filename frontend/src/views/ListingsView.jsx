import { useState } from "react";
import { motion } from "framer-motion";
import {
  PlusCircle, Store, LogIn, RefreshCw, Truck,
} from "lucide-react";
import { postJson } from "@/lib/api";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { OriginChip, originOf } from "@/components/ui/badges";
import { InfoTip } from "@/components/ui/fields";
import { ListingCard } from "@/components/ListingCard";
import { ListingCardSkeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  BoxIllustration, TagIllustration,
} from "@/components/ui/illustrations";
import { cn } from "@/lib/utils";
import { hasSalePrice, saleProceeds, soldUnits } from "@/lib/sales";

/* The listings pipeline: ONE view of the seller's whole store, cut by
   lifecycle tab. Rendered as the lower section of the merged Sell screen —
   drafts have their own strip above it (DraftsStrip), so there is no Drafts
   tab here (the "All" tab still mirrors every status, drafts included). */

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

export function ListingsView({ search = "" }) {
  const {
    listingsState, openListing, setView, startNew, user, openAuth, deleteListing,
    ebay, loadListings, metricsById, skippedDraftIds, storeSync, syncStore,
    listingsTab, setListingsTab, openShipping,
  } = useApp();
  const { confirm, toast } = useToast();

  // "drafts" was a tab here before the drafts strip existed; a stale saved
  // selection lands on Active.
  const tabId = (listingsTab === "drafts" ? "active" : listingsTab) || "active";
  const tab = TABS.find((t) => t.id === tabId) || TABS[0];
  const pick = (t) => setListingsTab(t);

  const counts = Object.fromEntries(TABS.map((t) => [
    t.id,
    t.statuses
      ? listingsState.items.filter((i) => t.statuses.includes(i.status)).length
      : listingsState.items.length,
  ]));

  // Manual re-run of the store mirror (the mirror itself runs at app load).
  const importFromEbay = async () => {
    const r = await syncStore({ force: true });
    if (!r) return;
    if (r.error) {
      toast(`Couldn't sync with eBay: ${r.error}`, { kind: "error" });
      return;
    }
    const fresh = r.imported || 0;
    // Duplicates from before the sync matched on eBay's item id: the sync
    // folds each pair back onto the listing this app created.
    const gone = r.deduped
      ? ` ${r.deduped} duplicate${r.deduped === 1 ? "" : "s"} merged.` : "";
    toast(
      fresh || r.updated || r.deduped
        ? `Synced ${r.found} eBay listing${r.found === 1 ? "" : "s"} — ${fresh} new, ${r.updated} updated.${gone}`
        : "Everything's already in sync with eBay.",
      { kind: "success" },
    );
    if (r.failed) {
      toast(`${r.failed} listing${r.failed === 1 ? "" : "s"} couldn't be read from eBay.`,
        { kind: "warning" });
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
      const res = await postJson("/api/ebay/end-listing", { session_id: item.id });
      await loadListings({ quiet: true });
      // Ending can discover the listing already finished on eBay — say where
      // it actually went (a sale files under Sold, not Inactive).
      toast(res.status === "sold"
        ? "Turns out this one sold on eBay — it's filed under Sold. 🎉"
        : "Listing ended — find it under Inactive.", { kind: "success" });
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

  // "Create Listing" from an empty tab used to look broken: this list now
  // lives on the Sell screen, so startNew() lands you where you already are
  // and nothing visibly happens. The uploader is at the top of this same
  // screen — take them to it.
  const go = () => {
    if (tab.empty.action?.go !== "new") return setView(tab.empty.action.go);
    startNew();
    try {
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) { window.scrollTo(0, 0); }
  };

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
            className="h-full"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, delay: Math.min(i * 0.03, 0.3) }}
          >
            <ListingCard className="h-full" item={item} onOpen={openListing} onDelete={askDelete}
              onEnd={(item.status === "published" || item.status === "live") ? askEnd : undefined}
              ending={endingId === item.id}
              skipped={skippedDraftIds.has(item.id)}
              stale={(item.status === "published" || item.status === "live")
                && dayAge(item.created_at) >= STALE_DAYS}
              metrics={metricsById[item.id]} />
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
        <div className="min-w-0 flex items-center gap-2">
          <h2 className="text-lg sm:text-xl font-bold tracking-tight text-ink">Listings</h2>
          <InfoTip text={tab.sub} />
        </div>
        <div className="flex items-center gap-2">
          {user && ebay.connected && (
            <Button variant="soft" onClick={() => openShipping()}>
              <Truck aria-hidden /> Ship orders
            </Button>
          )}
          {user && ebay.connected && (
            <Button variant="soft" onClick={importFromEbay} loading={storeSync.syncing}>
              <RefreshCw aria-hidden /> Sync with eBay
            </Button>
          )}
        </div>
      </div>

      {/* The pipeline: one tab per lifecycle stage, with live counts. "Finds"
          only appears once Shop Mode has produced any. */}
      <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 pb-0.5">
        {TABS.filter((t) => t.id !== "finds" || counts.finds > 0).map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => pick(t.id)}
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

      {/* Profit framework: on the Sold tab, total up what the items with a
          recorded cost basis made (sale − purchase price, before fees). */}
      {tabId === "sold" && hasItems && (() => {
        // Counts what the buyers PAID — an accepted offer settles below the
        // asking price, so totalling `price` here overstated every profit.
        const withCost = items.filter(
          (i) => i.listing?.purchase_price != null && saleProceeds(i.listing) > 0);
        if (!withCost.length) return null;
        const profit = withCost.reduce(
          (sum, i) => sum + (saleProceeds(i.listing)
            - Number(i.listing.purchase_price) * soldUnits(i.listing)), 0);
        const approx = withCost.filter((i) => !hasSalePrice(i.listing)).length;
        return (
          <p className="text-[13px] text-ink-secondary -mt-1 flex items-center gap-1.5">
            <strong className={profit >= 0 ? "text-success" : "text-warning"}>
              {profit >= 0 ? "+" : "−"}${Math.abs(profit).toFixed(2)} profit
            </strong>
            <span>· {withCost.length} item{withCost.length === 1 ? "" : "s"}</span>
            {approx > 0 && (
              <span>· {approx} at the asking price</span>
            )}
            <InfoTip text="What each item sold for minus what you paid, before fees & shipping — only items with a 'You paid' amount count. Where eBay hasn't reported the sale amount, the asking price stands in; set the real one in the editor's Pricing card." />
          </p>
        );
      })()}

      {/* Origin legend: which badges appear in this grid and what each one is
          allowed to do — hover (or long-press) a chip for the full rules. */}
      {showLegend && (() => {
        const present = [...new Set(items.map(originOf))];
        return (
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 text-[12px] text-ink-faint -mt-1">
            {present.map((k) => <OriginChip key={k} kind={k} />)}
            <InfoTip text="Hover a badge to see what that kind of listing can and can't do here." />
          </div>
        );
      })()}
      {body}
    </div>
  );
}

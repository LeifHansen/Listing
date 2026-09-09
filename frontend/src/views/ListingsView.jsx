import { useState } from "react";
import { motion } from "framer-motion";
import {
  PlusCircle, Store, LogIn, RefreshCw, Truck, AlertTriangle,
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
import { ViewToggle } from "@/components/ui/ViewToggle";
import { EmptyState } from "@/components/ui/EmptyState";
import { ListingsIllustration } from "@/components/ui/illustrations";
import { cn, formatMoney } from "@/lib/utils";
import { hasSalePrice, saleProceeds, soldUnits } from "@/lib/sales";
import {
  endedGraceDays, isDraft, keptWhenEnded, listingsView, orderListings,
} from "@/lib/listingsView";
import { DraftCategoryEdit } from "@/views/listing/CategoryQuickPick";
import { DraftFormatEdit } from "@/views/listing/FormatQuickPick";

/* The listings pipeline: ONE view of the seller's whole store, cut by
   lifecycle tab. Rendered as the lower section of the merged Sell screen —
   drafts have their own strip above it (DraftsStrip), so there is no Drafts
   tab here (the "All" tab holds every status, drafts and archive included). */

export const TABS = [
  {
    id: "active", label: "Active", statuses: ["published", "live"],
    sub: "Everything currently live on eBay — created here or imported",
    empty: {
      illustration: ListingsIllustration, title: "Nothing live yet",
      message: "Publish a draft (or create a listing from photos) and it shows up here the moment it's live.",
      action: { label: "Create Listing", icon: PlusCircle, go: "new" },
    },
  },
  {
    id: "finds", label: "Finds", statuses: ["unlisted"],
    sub: "Shop Mode finds waiting to become listings",
    empty: {
      illustration: ListingsIllustration, title: "No unlisted finds",
      message: "Scan items in Shop Mode while you're out hunting — tap Buy and they land here to finish later.",
      action: { label: "Open Shop Mode", icon: Store, go: "shop" },
    },
  },
  {
    // Everything finished, and nothing that isn't. What changed with the
    // automatic removal is what collects here: the store sync no longer
    // mirrors eBay's ended listings in at all (a card per listing that ever
    // finished, blank once eBay stopped serving its photos — the pile the
    // seller reported), and one of the seller's OWN that ends is kept here
    // for a month so it can be relisted, then removed on its own. Sales stay
    // for good.
    id: "inactive", label: "Inactive", statuses: ["ended", "sold"],
    sub: "The archive: every sale, plus your listings that ended — relist one "
      + "or leave it, we clear it out after a month",
    empty: {
      illustration: ListingsIllustration, title: "Nothing finished yet",
      message: "Sales collect here for good, with what each one went for. "
        + "Listings you end stay for a month in case you want to relist, "
        + "then they're removed on their own.",
    },
  },
  {
    // The whole store, so its count is what the other tabs add up to. It
    // used to leave the archive out, and the badges said so — "Active 355,
    // Inactive 11, All 355" was the report. The finished listings sit after
    // the live ones here (orderListings), not among them.
    id: "all", label: "All", statuses: null,
    sub: "Your whole store in one place — everything live, plus drafts, finds "
      + "and the archive (finished listings sit at the end)",
    empty: {
      illustration: ListingsIllustration, title: "No listings yet",
      message: "Let's create your first listing — snap a few photos and the AI writes the rest.",
      action: { label: "Create Listing", icon: PlusCircle, go: "new" },
    },
  },
];

// Which items a tab shows. `statuses` is a whitelist; a tab without one is
// the whole store. Nothing is subtracted from All any more: Active, Finds and
// Inactive are each a slice of it, so its count is their sum plus the drafts,
// and a finished listing is told apart by its card — a "Sold" or "Ended"
// badge, and "View sale" or "Relist" where a live card offers "Edit live".
export const inTab = (tab, item) => (tab.statuses
  ? tab.statuses.includes(item.status)
  : true);

// Tab ids this pipeline used to have, and where each one goes now. The
// selection is remembered across visits, so a seller who last left the app on
// a since-removed tab has to land somewhere sensible rather than on nothing.
export const STALE_TABS = { drafts: "active", sold: "inactive" };

// Active listings older than this get the amber "stale" clock: relisting
// fresh mints a new item id and a search-placement boost.
const STALE_DAYS = 60;
const dayAge = (iso) => (iso ? (Date.now() - Date.parse(iso)) / 86400000 : 0);

export function ListingsView({ search = "" }) {
  const {
    listingsState, openListing, setView, startNew, user, openAuth, deleteListing,
    rotateListingPhoto,
    ebay, loadListings, loadMoreListings, metricsById, skippedDraftIds,
    storeSync, syncStore,
    listingsTab, setListingsTab, openShipping, listingsLayout, setListingsLayout,
    health,
  } = useApp();
  const { confirm, toast } = useToast();

  const list = listingsLayout === "list";
  // One class for every collection this view renders (cards and skeletons
  // alike), so the two layouts can never drift apart.
  const gridClass = list
    ? "flex flex-col gap-2"
    : "grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4";

  // Stale saved selections from earlier versions of this pipeline: "drafts"
  // was a tab here before the drafts strip existed (→ Active), and "sold" was
  // its own tab before sold items were folded into the archive (→ Inactive).
  const tabId = STALE_TABS[listingsTab] || listingsTab || "active";
  const tab = TABS.find((t) => t.id === tabId) || TABS[0];
  const pick = (t) => setListingsTab(t);

  const counts = Object.fromEntries(TABS.map((t) => [
    t.id, listingsState.items.filter((i) => inTab(t, i)).length,
  ]));

  // Manual re-run of the store mirror (the mirror itself runs at app load).
  const importFromEbay = async () => {
    const r = await syncStore({ force: true });
    if (!r) return;
    if (r.error) {
      toast(`Couldn't sync with eBay: ${r.error}`, { kind: "error" });
      return;
    }
    // eBay stopped the pass part-way, so the counts describe a fraction of
    // the store. Saying "synced 400 listings" here — or worse, "everything's
    // already in sync" when nothing got through — reports a store that was
    // never read as one that was.
    if (r.rateLimited) {
      const wait = r.retryAfter
        ? ` Try again in about ${r.retryAfter} second${r.retryAfter === 1 ? "" : "s"}.`
        : " Try again shortly.";
      toast(
        `eBay limited how fast we could read your store, so this sync is `
        + `incomplete — ${r.imported || 0} new and ${r.updated || 0} updated so `
        + `far.${wait}`,
        { kind: "warning" });
      return;
    }
    const fresh = r.imported || 0;
    // Duplicates from before the sync matched on eBay's item id: the sync
    // folds each pair back onto the listing this app created.
    const gone = r.deduped
      ? ` ${r.deduped} duplicate${r.deduped === 1 ? "" : "s"} merged.` : "";
    // Cards this sync took OFF the screen: ended listings that ran out of
    // road — the store's mirrors of eBay's unsold ones, and any of the
    // seller's own past the grace period. Named rather than left to be
    // noticed, because a grid that quietly shrinks is the same event as one
    // that lost something — and "everything's already in sync" would be
    // untrue on a run that removed five of them.
    const cleared = r.removed
      ? ` ${r.removed} ended listing${r.removed === 1 ? "" : "s"} removed.` : "";
    toast(
      fresh || r.updated || r.deduped || r.removed
        ? `Synced ${r.found} eBay listing${r.found === 1 ? "" : "s"} — ${fresh} new, ${r.updated} updated.${gone}${cleared}`
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

  // End a live listing straight from its card. What happens to the card
  // afterwards depends on whose work is in it — kept under Inactive for the
  // grace period, or removed there and then for a record the store sync made
  // — so the dialog says which rather than promising an archive that may not
  // survive the night. (A sale is the exception to both, and ending can
  // discover one.)
  const [endingId, setEndingId] = useState(null);
  const askEnd = async (item) => {
    const name = item.listing?.title || item.title || "this listing";
    const kept = keptWhenEnded(item);
    const days = endedGraceDays(health);
    if (!(await confirm({
      title: "End this listing on eBay?",
      message: kept
        ? `"${name}" comes off eBay immediately and moves to Inactive, where `
          + `you can relist it. We clear it out after ${days} days.`
        : `"${name}" comes off eBay immediately, and its card is removed from `
          + "here — this one is a copy of your eBay listing, so there's "
          + "nothing of yours in it to keep.",
      confirmLabel: kept ? "End listing" : "End & remove",
      danger: true,
    }))) return;
    setEndingId(item.id);
    try {
      const res = await postJson("/api/ebay/end-listing", { session_id: item.id });
      await loadListings({ quiet: true });
      // The server's own answer decides the wording, not the guess the
      // dialog made: `removed` is whether the record actually went. Ending
      // can also discover the listing already sold, which is archived.
      toast(res.status === "sold"
        ? "Turns out this one sold on eBay — it's archived under Inactive. 🎉"
        : res.removed
          ? "Listing ended and removed."
          : res.status === "ended"
            ? "Listing ended — it's under Inactive if you want to relist it."
            : res.message || "Listing ended.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't end the listing: ${e.message}`, { kind: "error" });
    } finally {
      setEndingId(null);
    }
  };

  const q = search.trim().toLowerCase();
  // Newest first, except that a listing a buyer has bid or made an offer on
  // goes to the top and the archive goes to the bottom: see
  // lib/listingsView.orderListings. The card itself says why it is where it
  // is (the green glow and its chip, or a Sold / Ended badge, in ListingCard).
  const shown = listingsState.items
    .filter((i) => inTab(tab, i))
    .filter((i) => !q
      || (i.listing?.title || i.title || "").toLowerCase().includes(q)
      || (i.listing?.brand || "").toLowerCase().includes(q)
      || (i.listing?.description || "").toLowerCase().includes(q));
  const items = orderListings(shown, metricsById);

  // "Create Listing" from an empty tab used to look broken: this list now
  // lives on the Sell screen, so startNew() lands you where you already are
  // and nothing visibly happens. The uploader is at the top of this same
  // screen — take them to it.
  const go = () => {
    // Guard the whole thing, not just the read: the Inactive tab has no
    // `action`, and `?.go !== "new"` is TRUE for undefined, which walked
    // straight into dereferencing it.
    const action = tab.empty.action;
    if (!action) return;
    if (action.go !== "new") return setView(action.go);
    startNew();
    try {
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) { window.scrollTo(0, 0); }
  };

  const view = listingsView({
    ...listingsState, user, count: listingsState.items.length,
  });

  let body;
  if (listingsState.loading && !listingsState.loaded) {
    body = (
      <div className={gridClass}>
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
  } else if (view.kind === "unavailable") {
    // Not the empty state. "No listings yet" is a claim about the seller's
    // account, and a read that failed is not evidence for it.
    body = (
      <Card>
        <p className="text-sm text-ink flex gap-2">
          <AlertTriangle size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
          <span>{view.message}</span>
        </p>
        <Button variant="soft" size="sm" className="mt-3"
          onClick={() => loadListings()}>
          Try again
        </Button>
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
      <div className={gridClass}>
        {items.map((item, i) => (
          <motion.div
            key={item.id}
            className={list ? undefined : "h-full"}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, delay: Math.min(i * 0.03, 0.3) }}
          >
            <ListingCard className={list ? undefined : "h-full"} layout={listingsLayout}
              item={item} onOpen={openListing} onDelete={askDelete}
              onEnd={(item.status === "published" || item.status === "live") ? askEnd : undefined}
              ending={endingId === item.id}
              /* Drafts only, like the category and format controls below: a
                 live listing's photos are the copy eBay already took. */
              onRotate={isDraft(item) ? rotateListingPhoto : undefined}
              skipped={skippedDraftIds.has(item.id)}
              stale={(item.status === "published" || item.status === "live")
                && dayAge(item.created_at) >= STALE_DAYS}
              metrics={metricsById[item.id]} />
            {/* Drafts carry their category on the card here too — the "All"
                tab mixes them in with live listings, and a draft is exactly
                where the category is still wrong and still free to fix. It
                also decides which conditions eBay accepts, so it is the one
                field worth fixing before Publish. */}
            {isDraft(item) && (
              <DraftCategoryEdit item={item} className={cn("mt-1.5", list && "sm:w-72")} />
            )}
            {/* And how it sells. The same one control the drafts strip and
                the dashboard carry, on drafts only — a live listing's format
                is fixed once eBay has accepted it. */}
            {isDraft(item) && (
              <DraftFormatEdit item={item} className={cn("mt-1.5", list && "sm:w-72")} />
            )}
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
          <h2 className="text-lg sm:text-xl font-bold text-ink">Listings</h2>
          <InfoTip text={tab.sub} />
        </div>
        {/* Wraps like its parent does. Without it the toggle plus "Ship
            orders" plus "Sync with eBay" measured 442px against a 375px
            viewport, so the Sell screen scrolled sideways on a phone. */}
        <div className="flex flex-wrap items-center justify-end gap-2">
          {/* Grid or list — a viewing preference, so it sits with the other
              view-level controls and is remembered across visits. */}
          <ViewToggle value={listingsLayout} onChange={setListingsLayout} />
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
              "font-display tabular-nums text-[11px] font-bold rounded-full px-1.5 min-w-5 h-5 grid place-items-center",
              tabId === t.id ? "bg-white/20" : "bg-bg-sunken",
            )}>
              {/* Same rule as the card below and the dashboard tiles: these
                  are counted off a page that a failed read left empty, so
                  during an outage every tab would badge a confident 0 --
                  directly above a card explaining that the listings could
                  not be loaded. A number nobody could measure is a dash. */}
              {view.kind === "unavailable" ? "—" : counts[t.id]}
            </span>
          </button>
        ))}
      </div>

      {/* Profit framework: on the archive tab, total up what the SOLD items
          with a recorded cost basis made (sale − purchase price, before
          fees). Ended-unsold listings share this tab and made nothing, so
          they are excluded rather than counted as zeroes. */}
      {tabId === "inactive" && hasItems && (() => {
        // Counts what the buyers PAID — an accepted offer settles below the
        // asking price, so totalling `price` here overstated every profit.
        const withCost = items.filter(
          (i) => i.status === "sold" && i.listing?.purchase_price != null
            && saleProceeds(i.listing) > 0);
        if (!withCost.length) return null;
        const profit = withCost.reduce(
          (sum, i) => sum + (saleProceeds(i.listing)
            - Number(i.listing.purchase_price) * soldUnits(i.listing)), 0);
        const approx = withCost.filter((i) => !hasSalePrice(i.listing)).length;
        return (
          <p className="text-[13px] text-ink-secondary -mt-1 flex items-center gap-1.5">
            <strong className={profit >= 0 ? "text-success" : "text-warning"}>
              {profit >= 0 ? "+" : "−"}{formatMoney(Math.abs(profit), withCost[0].listing?.currency)} profit
            </strong>
            <span>· {withCost.length} item{withCost.length === 1 ? "" : "s"}</span>
            {approx > 0 && (
              <span>· {approx} at the asking price</span>
            )}
            <InfoTip text="What each item sold for minus what you paid, before fees & shipping — only items with a 'You paid' amount count. Where eBay hasn't reported the sale amount, the asking price stands in; open the sold listing and set the real one under Sale figures." />
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
      {view.notice && (
        <p className="text-sm rounded-tile border border-warning/30 bg-warning-soft p-3 text-ink flex flex-wrap items-center gap-2">
          <AlertTriangle size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
          <span>{view.notice}</span>
          {/* The way through. Without it the notice is honest and useless:
              the older listings are not on the page, not in the tab counts,
              and not findable by the search box, which filters what is
              loaded. Appending, so everything above keeps working and only
              ever sees more. */}
          {listingsState.nextCursor && (
            <Button size="sm" variant="soft" loading={listingsState.loadingMore}
                    onClick={loadMoreListings}>
              Load older listings
            </Button>
          )}
        </p>
      )}
      {body}
    </div>
  );
}

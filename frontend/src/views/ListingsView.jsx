import { lazy, Suspense, useCallback, useMemo, useState } from "react";
import { motion } from "framer-motion";
import {
  PlusCircle, Store, LogIn, AlertTriangle, ArrowRightLeft, RefreshCcw,
  TrendingDown, FolderInput, HandCoins, CircleStop, BadgePercent, BadgeX, Tag,
} from "lucide-react";
import { pollJob, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { BulkAmountPanel } from "@/components/ui/BulkAmountPanel";
import { DiscountPanel } from "@/components/bulk/DiscountPanel";
import { AddToSalePanel } from "@/components/bulk/AddToSalePanel";
import { SaleDialog } from "@/views/sales/SaleDialog";
import { LiveBulkBar } from "@/components/LiveBulkBar";
import { StoreCategoryPanel } from "@/components/bulk/StoreCategoryPanel";
import { ListingCard } from "@/components/ListingCard";
import { ListingFilters } from "@/components/ListingFilters";
import { SavedViewTabs } from "@/components/SavedViewTabs";
import { ListingCardSkeleton } from "@/components/ui/Skeleton";
import { ViewToggle } from "@/components/ui/ViewToggle";
import { Segmented } from "@/components/ui/Segmented";
import { EmptyState } from "@/components/ui/EmptyState";
import { ListingsIllustration } from "@/components/ui/illustrations";
import { cn, formatMoney } from "@/lib/utils";
import { bulkToast, endingSummary } from "@/lib/bulkActions";
import { hasSalePrice, saleProceeds, soldUnits } from "@/lib/sales";
import {
  endedGraceDays, inMarketFilter, isDraft, isLive, keptWhenEnded, listedAt,
  listingsView, MARKET_FILTERS,
} from "@/lib/listingsView";
import { filterListings, isEmptyFilters } from "@/lib/listingFilters";
import { sortListings, sortMeta } from "@/lib/listingSort";
import { ebayCategoryOptions, storeCategoryOptions } from "@/lib/categoryOptions";
import { useStoreCategories } from "@/lib/useStoreCategories";
import { DraftCategoryEdit } from "@/views/listing/CategoryQuickPick";
import { DraftFormatEdit } from "@/views/listing/FormatQuickPick";
import { DraftPriceEdit } from "@/views/listing/PriceQuickEdit";
import { canQuickEdit, QuickEditPanel } from "@/views/listing/QuickEdit";

// Fetched when the seller first opens it, not with the app: only a seller
// with Etsy connected can, and most of them only now and then.
const CrosspostWizard = lazy(() => import("@/views/crosspost/CrosspostWizard")
  .then((m) => ({ default: m.CrosspostWizard })));

/* The listings pipeline: ONE view of the seller's whole store, cut by
   lifecycle tab. It IS the Manage tab now (ManageView wraps it with the page
   header and the whole-store actions) rather than the bottom section of a
   merged screen. Drafts live on the List tab with the uploader that makes
   them, so there is no Drafts tab here — the "All" tab holds every status,
   drafts and archive included.

   What is on screen above the grid, top to bottom, and nothing else:

     1. the lifecycle tabs, with the grid/list toggle at their right end;
        and the seller's saved views after a divider (SavedViewTabs);
     2. the filter bar — Filters, the store / eBay category pickers, Sort,
        and (for a seller on more than one marketplace) where-it-lives at
        its right;
     3. the chips for what is on, "Save view", and "Showing N of M";
     4. the selection bar, only while something is ticked;
     5. the "older listings" notice, only when the page is cut.

   Each row is one kind of thing, in one visual vocabulary. The first draft
   of this screen had six rows here in three accent colours and two pill
   heights, glued together with negative margins, and read as noise before a
   single card. */

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
    // "Everything you sold", not "every sale": Manage has a Sales section
    // now, and it means eBay sale EVENTS (discounts), not sold items.
    sub: "Everything you sold, plus your listings that ended — relist one or "
      + "leave it, we clear it out after a month",
    empty: {
      illustration: ListingsIllustration, title: "Nothing finished yet",
      message: "What you sell collects here for good, with what each one went for. "
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
    sub: "Your whole store — everything live, plus drafts, finds and the "
      + "archive (finished listings sit at the end)",
    empty: {
      illustration: ListingsIllustration, title: "No listings yet",
      message: "Let's create your first eBay listing — snap a few photos and the AI writes the rest.",
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

// The number "Lower prices…" needs before it can run. The 75% ceiling is the
// server's (bulk_actions.MAX_PERCENT): past it a bulk cut is more likely a
// slipped decimal than an intention, and the editor is there for a genuine
// clearance on one listing.
const LOWER_PRICES = {
  unit: "%", initial: 10, min: 1, max: 75, step: 1,
  label: "Lower each ticked price by",
  submit: (n, value) => `Lower ${n} price${n === 1 ? "" : "s"} by ${value}%`,
  note: "New prices go straight to eBay. Anything that has sold or ended since "
    + "you ticked it is skipped.",
};

// "Send offers to watchers…": eBay's own floor (5%) and this app's ceiling
// (50%), the same as the dashboard's group button — a buyer takes one of
// these with a single tap, so the ceiling is lower than a price cut's.
const SEND_OFFERS = {
  unit: "%", initial: 10, min: 5, max: 50, step: 1,
  label: "Offer each ticked listing's watchers",
  submit: (n, value) => `Send ${value}% off to ${n === 1 ? "its" : `${n} listings'`} watchers`,
  note: "A private offer to the buyers eBay says are interested. Your listed "
    + "price doesn't change. Listings nobody is watching are skipped.",
};

export function ListingsView({ search = "" }) {
  const {
    listingsState, openListing, setView, startNew, user, openAuth, deleteListing,
    rotateListingPhoto, duplicateListing,
    ebay, loadListings, loadMoreListings, metricsById, skippedDraftIds,
    listingsTab, setListingsTab, listingsLayout, setListingsLayout,
    listingFilters, clearListingFilters, listingsSort,
    saleEvents, saleByListing, adoptSaleEvents,
    health,
    listingsMarket, setListingsMarket, liveSelection, setLiveSelection,
    marketplaces, connectedMarketplaces,
  } = useApp();
  const { confirm, toast } = useToast();

  const list = listingsLayout === "list";
  // One class for every collection this view renders (cards and skeletons
  // alike), so the two layouts can never drift apart. The card grid has no
  // row gap of its own: each card brings the space under it (see the cell
  // below), because that space is also where its Quick edit panel opens.
  const gridClass = list
    ? "flex flex-col gap-2"
    : "grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-x-4";

  // Stale saved selections from earlier versions of this pipeline: "drafts"
  // was a tab here before the drafts strip existed (→ Active), and "sold" was
  // its own tab before sold items were folded into the archive (→ Inactive).
  const tabId = STALE_TABS[listingsTab] || listingsTab || "active";
  const tab = TABS.find((t) => t.id === tabId) || TABS[0];
  const pick = (t) => setListingsTab(t);

  const counts = Object.fromEntries(TABS.map((t) => [
    t.id, listingsState.items.filter((i) => inTab(t, i)).length,
  ]));

  // The second cut, WHERE a listing lives, appears once there is a second
  // marketplace to cut by: one connected, or one already on a record. An
  // eBay-only seller sees the pipeline exactly as before.
  const otherMarketplaces = connectedMarketplaces.some((m) => m.key !== "ebay")
    || listingsState.items.some((i) => Object.keys(i.listing?.marketplaces || {})
      .some((k) => k !== "ebay"));
  const marketId = otherMarketplaces
    && MARKET_FILTERS.some((f) => f.id === listingsMarket) ? listingsMarket : "all";
  const marketCounts = Object.fromEntries(MARKET_FILTERS.map((f) => [
    f.id, listingsState.items.filter((i) => inTab(tab, i) && inMarketFilter(f.id, i)).length,
  ]));
  const etsy = marketplaces.find((m) => m.key === "etsy");
  const [crosspostOpen, setCrosspostOpen] = useState(false);

  // useCallback, because ListingCard is memo'd and its comment says why: the
  // app context re-renders this whole view on every background poll, so a
  // handler with a fresh identity per render fails the memo comparison for
  // every card on screen and reconciles the entire grid for a bell badge.
  // Every other callback the card takes (openListing, deleteListing,
  // rotateListingPhoto, confirm, toast) is already stable -- these two were
  // the pair defeating it.
  const askDelete = useCallback(async (item) => {
    const name = item.listing?.title || item.title || "this listing";
    if (await confirm({
      title: "Delete this listing?",
      message: `"${name}" will be permanently removed. This can't be undone.`,
      confirmLabel: "Delete",
      danger: true,
    })) deleteListing(item.id);
  }, [confirm, deleteListing]);

  // End a live listing straight from its card. What happens to the card
  // afterwards depends on whose work is in it — kept under Inactive for the
  // grace period, or removed there and then for a record the store sync made
  // — so the dialog says which rather than promising an archive that may not
  // survive the night. (A sale is the exception to both, and ending can
  // discover one.)
  const [endingId, setEndingId] = useState(null);
  const askEnd = useCallback(async (item) => {
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
  }, [confirm, health, loadListings, toast]);

  const q = search.trim().toLowerCase();
  // The tab, the marketplace and the search box: each about one field, and
  // all of them BEFORE the filter bar — so the category pickers can count
  // their choices off this list and a tick in one does not empty the rest.
  const base = useMemo(() => listingsState.items
    .filter((i) => inTab(tab, i))
    .filter((i) => inMarketFilter(marketId, i))
    .filter((i) => !q
      || (i.listing?.title || i.title || "").toLowerCase().includes(q)
      || (i.listing?.brand || "").toLowerCase().includes(q)
      || (i.listing?.description || "").toLowerCase().includes(q)),
  [listingsState.items, tab, marketId, q]);

  // The filter bar's cut on top, then the order. Every predicate lives in
  // lib/listingFilters and every order in lib/listingSort, so the grid, the
  // "showing N of M" line and the empty state below all ask the same
  // question of the same list. The default order ("Recommended") is the one
  // the grid always had: a listing a buyer has bid or made an offer on goes
  // to the top and the archive to the bottom (lib/listingsView.orderListings),
  // and the card itself says why it is where it is.
  // Memoized for the same reason as the drafts grid above it on this screen:
  // passes and a sort over every listing the seller owns, re-run on every
  // render, and the app context hands this component a new value on each
  // background poll whether or not a listing changed.
  const items = useMemo(() => sortListings(
    // `now` left to its default: the age windows measure against the moment
    // this list is worked out, and the memo above decides when that is.
    filterListings(base, listingFilters, undefined, { metricsById, saleByListing }),
    listingsSort, metricsById,
  ), [base, listingFilters, listingsSort, metricsById, saleByListing]);

  // The category pickers' choices, counted off `base` (see above).
  const storeCats = useStoreCategories();
  const storeOptions = useMemo(
    () => storeCategoryOptions(storeCats.categories, base),
    [storeCats.categories, base]);
  const ebayOptions = useMemo(() => ebayCategoryOptions(base), [base]);
  // Only for a seller with shelves to pick from — or a listing already on
  // one, which is how a store that could not be read still gets the filter.
  const showStore = storeCats.categories.length > 0
    || base.some((i) => String(i.listing?.store_category_id || "").replace(/^0$/, ""));

  // Ticks on the live listings, on the tabs that show them. They used to
  // wait for a second marketplace, because the crosspost was the only thing
  // a tick could do; End & relist and Lower prices work on any live eBay
  // listing, so every seller gets the ticks now and only the crosspost button
  // still waits for somewhere else to post to.
  //
  // Off `items`, so a filtered grid ticks what it is SHOWING. "Select all"
  // over listings the seller cannot see is how a bulk action reaches an item
  // they had deliberately filtered out.
  const selectable = tabId === "active" || tabId === "all";
  const liveItems = selectable ? items.filter(isLive) : [];
  const selectedLive = liveItems.filter((i) => liveSelection[i.id]);
  const allLiveSelected = liveItems.length > 0 && selectedLive.length === liveItems.length;
  const toggleLive = useCallback((id) => setLiveSelection((s) => ({ ...s, [id]: !s[id] })),
    [setLiveSelection]);
  const toggleAllLive = () => setLiveSelection(allLiveSelected
    ? {} : Object.fromEntries(liveItems.map((i) => [i.id, true])));
  const clearLive = () => setLiveSelection({});

  // The bulk job running off the ticks, or null: { verb, done, total, title }.
  // Every action on the bar is minutes of serial eBay calls the server runs
  // in the background and this polls, so the bar can say which listing it is
  // on rather than sit on a spinner. One at a time — the server holds one
  // reservation per account for each, and a second job from the same bar
  // while one runs would be two runs over the same ticks.
  const [running, setRunning] = useState(null);
  // Which action's panel is open under the bar ("lower", "offers", "shelf"),
  // or null. One at a time: two half-filled amounts under one bar is two
  // questions with one set of ticks.
  const [openPanel, setOpenPanel] = useState(null);
  const togglePanel = (key) => setOpenPanel((cur) => (cur === key ? null : key));

  /* Start a bulk job over the ticks and see it through: POST, poll, toast
     the report, clear the ticks, reload. `did(n, res)` words the changed
     ones; `fail` is the sentence for a run that could not start or finish. */
  const runJob = async ({ verb, url, body = {}, did, empty, fail }) => {
    const picked = selectedLive;
    if (!picked.length || running) return;
    setOpenPanel(null);
    setRunning({ verb, done: 0, total: picked.length, title: "" });
    try {
      const start = await postJson(url, { ...body, listing_ids: picked.map((i) => i.id) });
      const total = start.total || picked.length;
      const res = await pollJob(start.job_id, {
        onUpdate: (j) => setRunning({
          verb, done: j.current || 0, total: j.total_items || total,
          title: j.current_title || "",
        }),
      });
      toast(...bulkToast(res, { did, empty }));
      clearLive();
      await loadListings({ quiet: true });
    } catch (e) {
      toast(`${fail}: ${e.message}`, { kind: "error" });
      // Some may have gone through before the failure: show what is true.
      loadListings({ quiet: true });
    } finally {
      setRunning(null);
    }
  };

  // End & relist: every ticked listing comes off eBay and goes straight back
  // up as a NEW listing — a new item id, a new "listed" date, and a fresh
  // AI-written title and description so it reads as new to the buyers eBay
  // already showed the old one.
  const endAndRelist = async () => {
    const picked = selectedLive;
    if (!picked.length || running) return;
    const n = picked.length;
    const one = n === 1 ? `"${picked[0].listing?.title || picked[0].title || "this listing"}"` : null;
    if (!(await confirm({
      title: n === 1 ? "End and relist this listing?" : `End and relist ${n} listings?`,
      message: (one ? `${one} comes` : "Each one comes")
        + " off eBay and goes straight back up as a new listing, with a "
        + "fresh AI-written title and description — same photos, price and "
        + "details. A new listing starts over on views, watchers and "
        + "sales history, and the old one moves to Inactive.",
      confirmLabel: n === 1 ? "End & relist" : `End & relist ${n}`,
      danger: true,
    }))) return;
    await runJob({
      verb: "Relisting", url: "/api/ebay/end-and-relist",
      did: (c) => `Relisted ${c} listing${c === 1 ? "" : "s"} with fresh copy`,
      empty: "Nothing to relist.", fail: "Couldn't end and relist",
    });
  };

  // Lower prices: every ticked listing's price cut by one percentage and
  // pushed to eBay, as a job this polls. It used to be a suggestion on the
  // dashboard ("Lower prices · 12", with a "Lower all…" on the group), and
  // that is gone on request: whether a price should come down is the
  // seller's call, so the cut lives here, on the listings they ticked, and
  // nowhere nags about it. The percentage is asked for in a panel under the
  // bar rather than a dialog, so the ticks stay in view while it is chosen.
  const lowerPrices = (percent) => runJob({
    verb: "Lowering", url: "/api/ebay/lower-prices", body: { percent },
    // The run's own percentage: a press that found one already going is
    // handed that run, which may have been started at a different number.
    did: (c, res) => `Lowered ${c} price${c === 1 ? "" : "s"} by ${res.percent ?? percent}%`,
    fail: "Couldn't lower prices",
  });

  // Send offers to watchers: the dashboard's "Send offers", over the ticks.
  // The listed price does not move — the discount goes privately to the
  // buyers eBay says are interested.
  const sendOffers = (percent) => runJob({
    verb: "Offering", url: "/api/ebay/send-offers-ticked", body: { percent },
    did: (c, res) => `Sent ${res.percent ?? percent}% off to the watchers of ${c} listing${c === 1 ? "" : "s"}`,
    empty: "No offers sent.", fail: "Couldn't send offers",
  });

  // Move to store category: the ticked listings onto one shelf of the
  // seller's eBay Store, each revised on eBay with the shelf and nothing else.
  const moveToShelf = (shelf) => runJob({
    verb: "Moving", url: "/api/ebay/move-store-category",
    body: { store_category_id: String(shelf.id) },
    did: (c, res) => `Moved ${c} listing${c === 1 ? "" : "s"} to “${res.store_category_name || shelf.name}”`,
    fail: "Couldn't move these listings",
  });

  // End listings: the card's own End, over the ticks. What becomes of each
  // card depends on whose work is in it (keptWhenEnded), so the dialog says
  // which rather than promising an archive the mirrors will not get.
  const endListings = async () => {
    const picked = selectedLive;
    if (!picked.length || running) return;
    const n = picked.length;
    if (!(await confirm({
      title: n === 1 ? "End this listing on eBay?" : `End ${n} listings on eBay?`,
      message: endingSummary(picked, endedGraceDays(health)),
      confirmLabel: n === 1 ? "End listing" : `End ${n} listings`,
      danger: true,
    }))) return;
    await runJob({
      verb: "Ending", url: "/api/ebay/end-listings",
      did: (c) => `Ended ${c} listing${c === 1 ? "" : "s"}`,
      empty: "Nothing to end.", fail: "Couldn't end these listings",
    });
  };

  // ---- the sale actions: eBay sale events over the ticks ----------------
  // Synchronous calls (one eBay request covers the lot), so the bar shows a
  // "Starting the sale…" line rather than a per-listing count, and every
  // answer carries the whole list of sales, which the store adopts.
  const [saleDialog, setSaleDialog] = useState(false);
  // How many ticks are already in a sale the app can move them out of — the
  // only time the "move them" switch is worth offering.
  const inEditableSale = selectedLive.filter((i) => saleByListing.get(i.id)?.editable);
  const inAnySale = selectedLive.filter((i) => saleByListing.has(i.id));

  const runSale = async ({ verb, url, body, did, empty, fail }) => {
    const picked = selectedLive;
    if (!picked.length || running) return false;
    setOpenPanel(null);
    setRunning({ verb, done: 0, total: picked.length, title: "", single: true });
    try {
      const res = await postJson(url, { ...body, listing_ids: picked.map((i) => i.id) });
      adoptSaleEvents(res);
      toast(...bulkToast(res.report || {}, { did: (n) => did(n, res), empty }));
      if ((res.report || {}).changed) clearLive();
      return true;
    } catch (e) {
      toast(`${fail}: ${e.message}`, { kind: "error" });
      return false;
    } finally {
      setRunning(null);
    }
  };

  const applyDiscount = ({ percent, days, move }) => runSale({
    verb: "Starting the sale", url: "/api/sale-events", body: { percent, days, move },
    did: (n, res) => `${n} listing${n === 1 ? "" : "s"} on sale at ${percent}% off`
      + (res.event ? ` — “${res.event.name}”` : ""),
    empty: "None of those could go on sale.", fail: "Couldn't start the sale",
  });

  const addToSale = ({ event, move }) => runSale({
    verb: "Adding to the sale", url: `/api/sale-events/${encodeURIComponent(event.id)}/listings`,
    body: { move },
    did: (n) => `Added ${n} listing${n === 1 ? "" : "s"} to “${event.name}”`,
    empty: "None of those could go in that sale.", fail: "Couldn't add to the sale",
  });

  const removeFromSale = async () => {
    const n = inAnySale.length;
    if (!n || running) return;
    if (!(await confirm({
      title: n === 1 ? "Take this listing out of its sale?" : `Take ${n} listings out of their sales?`,
      message: "They go back to full price on eBay straight away. A sale left "
        + "with nothing in it is ended.",
      confirmLabel: "Take out of sale",
      danger: true,
    }))) return;
    await runSale({
      verb: "Taking out of the sale", url: "/api/sale-events/remove-listings", body: {},
      did: (c) => `Took ${c} listing${c === 1 ? "" : "s"} out of ${c === 1 ? "its sale" : "their sales"}`,
      empty: "Nothing to take out.", fail: "Couldn't take them out of the sale",
    });
  };

  // "New sale…" from the Add to sale panel: the full dialog, over the ticks.
  const createSale = async (body) => {
    try {
      const res = await postJson("/api/sale-events", body);
      adoptSaleEvents(res);
      toast(...bulkToast(res.report || {}, {
        did: (n) => `“${res.event?.name}” — ${n} listing${n === 1 ? "" : "s"} at ${res.event?.percent_off}% off`,
        empty: "None of those could go in a sale.",
      }));
      if (res.event) clearLive();
      return !!res.event;
    } catch (e) {
      toast(`Couldn't start the sale: ${e.message}`, { kind: "error" });
      return false;
    }
  };

  // Which cards have their Quick edit panel open. Several at once is fine —
  // a seller going down a row fixing prices should not lose one panel's
  // typing by opening the next. Kept here rather than in each card because
  // the panel is laid out here, under the card (see the grid cell below).
  // Stable toggle, for the memo'd card (see askDelete above).
  const [quickEditing, setQuickEditing] = useState({});
  const toggleQuickEdit = useCallback(
    (id) => setQuickEditing((s) => ({ ...s, [id]: !s[id] })), []);

  // "Create Listing" from an empty tab. This used to need a scroll: the
  // uploader was at the top of the SAME screen, so startNew() landed you
  // where you already were and nothing visibly happened. It is a different
  // tab now, so the plain navigation is the whole job — and startNew clears
  // any open editor on the way, which setView alone would not.
  const go = () => {
    // Guard the whole thing, not just the read: the Inactive tab has no
    // `action`, and `?.go !== "new"` is TRUE for undefined, which walked
    // straight into dereferencing it.
    const action = tab.empty.action;
    if (!action) return;
    if (action.go === "new") return startNew();
    setView(action.go);
  };

  const view = listingsView({
    ...listingsState, user, count: listingsState.items.length,
  });

  let body;
  if (listingsState.loading && !listingsState.loaded) {
    body = (
      <div className={gridClass}>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <ListingCardSkeleton key={i} className={list ? undefined : "mb-4"} />
        ))}
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
    // Three empties, and they are not the same claim. "No listings yet" is
    // about the ACCOUNT; a tab emptied by the search box or the filter bar is
    // about the question that was asked, and it has to say so and offer the
    // way back — otherwise a seller who forgot a filter was on is looking at
    // an empty store with a button to create their first listing.
    const filtered = !isEmptyFilters(listingFilters);
    body = (
      <Card className="p-0">
        <EmptyState
          illustration={tab.empty.illustration}
          title={q || filtered ? "No matches" : tab.empty.title}
          message={
            q && filtered
              ? `Nothing here matches "${search}" with these filters on.`
              : filtered
                ? "No listings on this tab match the filters you've set."
                : q
                  ? `Nothing matches "${search}" here.`
                  : tab.empty.message
          }
          action={filtered ? (
            <Button variant="soft" size="lg" onClick={clearListingFilters}>
              Clear filters
            </Button>
          ) : (!q && tab.empty.action && (
            <Button variant="primary" size="lg" onClick={go}>
              <tab.empty.action.icon aria-hidden /> {tab.empty.action.label}
            </Button>
          ))}
        />
      </Card>
    );
  } else {
    body = (
      <div className={gridClass}>
        {items.map((item, i) => (
          <motion.div
            key={item.id}
            /* Two rows of the grid per card, shared across the row of cards
               (subgrid): the card, then whatever opens under it. The cards
               in a row still line up — the first track is as tall as the
               tallest of them — and a Quick edit panel opening under one
               card grows only the second track, so the cards beside it keep
               their size instead of stretching to match it. */
            className={list ? undefined : "row-span-2 grid grid-rows-subgrid"}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, delay: Math.min(i * 0.03, 0.3) }}
          >
            <ListingCard className={list ? undefined : "h-full"} layout={listingsLayout}
              item={item} onOpen={openListing} onDelete={askDelete}
              onEnd={isLive(item) ? askEnd : undefined}
              ending={endingId === item.id}
              /* Draft and live cards: a sold record offers Relist from its
                 archive instead, and a find was never a listing to copy. */
              onDuplicate={(isDraft(item) || isLive(item) || item.status === "ended")
                ? duplicateListing : undefined}
              /* Drafts only, like the category and format controls below: a
                 live listing's photos are the copy eBay already took. */
              onRotate={isDraft(item) ? rotateListingPhoto : undefined}
              skipped={skippedDraftIds.has(item.id)}
              stale={isLive(item)
                && dayAge(listedAt(item)) >= STALE_DAYS}
              meta={sortMeta(item, listingsSort)}
              sale={saleByListing.get(item.id)}
              metrics={metricsById[item.id]}
              showEbayChip={otherMarketplaces}
              selectable={selectable && isLive(item)}
              selected={!!liveSelection[item.id]}
              onSelect={toggleLive}
              /* Everything the grid can still change: live listings, drafts
                 and finds. A sale or an ended listing opens the editor, as
                 it always has — eBay does not revise a finished item. */
              onQuickEdit={canQuickEdit(item) ? toggleQuickEdit : undefined}
              quickEditOpen={!!quickEditing[item.id]} />
            {/* Under the card. In the grid this is the card's second track,
                and it carries the space down to the next row even when it
                holds nothing (pb-4, the row gap the grid does not have). */}
            <div className={cn(
              "flex flex-col gap-1.5 pt-1.5",
              list ? "empty:hidden" : "pb-4 empty:pt-0",
            )}>
              {/* The Quick edit panel, straight under the toggle that opened
                  it. It saves a draft; on a live listing it saves AND sends
                  the change to eBay, so the card and the listing never
                  disagree (see QuickEdit). */}
              {quickEditing[item.id] && canQuickEdit(item) && (
                <QuickEditPanel item={item} layout={listingsLayout}
                  onClose={() => toggleQuickEdit(item.id)} />
              )}
              {/* Drafts carry their category on the card here too — the "All"
                  tab mixes them in with live listings, and a draft is exactly
                  where the category is still wrong and still free to fix. It
                  also decides which conditions eBay accepts, so it is the one
                  field worth fixing before Publish. */}
              {isDraft(item) && (
                <DraftCategoryEdit item={item} className={cn(list && "sm:w-72")} />
              )}
              {/* And how it sells. The same one control the drafts strip and
                  the dashboard carry, on drafts only — a live listing's format
                  is fixed once eBay has accepted it. */}
              {isDraft(item) && (
                <DraftFormatEdit item={item} className={cn(list && "sm:w-72")} />
              )}
              {/* And what it asks — the field its format actually uses, plus
                  eBay's own comps behind one tap. Drafts only here: a live
                  listing's price is changed from its Quick edit, which sends
                  it to eBay in the same breath — a number changed by this
                  control would be saved here and nowhere else. */}
              {isDraft(item) && (
                <DraftPriceEdit item={item} className={cn(list && "sm:w-72")} />
              )}
            </div>
          </motion.div>
        ))}
      </div>
    );
  }

  const hasItems = user && listingsState.dbConfigured && items.length > 0;
  const needsEbay = ebay.connected ? undefined : "Connect eBay under Settings first.";

  // Where a listing lives — the second cut, for a seller on more than one
  // marketplace. "eBay only" is the crosspost's own shopping list. Rendered
  // at the right end of the filter row, in the same pill-in-pill shape as
  // the layout toggle, with a neutral pressed state: blue is the lifecycle
  // tab's, and a second blue row made two kinds of tab look like one.
  const marketControl = otherMarketplaces && (
    <Segmented label="Where a listing lives" value={marketId} onChange={setListingsMarket}
      options={MARKET_FILTERS.map((f) => ({
        id: f.id, label: f.label,
        title: view.kind === "unavailable" ? undefined : `${marketCounts[f.id]} on this tab`,
      }))} />
  );

  // Profit framework: on the archive tab, total up what the SOLD items with a
  // recorded cost basis made (sale − purchase price, before fees). Ended
  // listings share this tab and made nothing, so they are excluded rather
  // than counted as zeroes. Counts what the buyers PAID — an accepted offer
  // settles below the asking price, so totalling `price` here overstated
  // every profit.
  const profit = tabId === "inactive" && hasItems ? (() => {
    const withCost = items.filter(
      (i) => i.status === "sold" && i.listing?.purchase_price != null
        && saleProceeds(i.listing) > 0);
    if (!withCost.length) return null;
    const total = withCost.reduce(
      (sum, i) => sum + (saleProceeds(i.listing)
        - Number(i.listing.purchase_price) * soldUnits(i.listing)), 0);
    const approx = withCost.filter((i) => !hasSalePrice(i.listing)).length;
    return { total, count: withCost.length, approx, currency: withCost[0].listing?.currency };
  })() : null;

  return (
    <div className="flex flex-col gap-4">
      {/* The pipeline: one tab per lifecycle stage, with live counts, and the
          grid/list toggle at the row's right end — a viewing preference that
          is remembered across visits. "Finds" only appears once Shop Mode has
          produced any. Each tab says what it holds on hover; the row used to
          end in an ⓘ carrying the same sentence. */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-1.5 overflow-x-auto min-w-0 -mx-1 px-1 pb-0.5">
          {TABS.filter((t) => t.id !== "finds" || counts.finds > 0).map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => pick(t.id)}
              aria-pressed={tabId === t.id}
              title={t.sub}
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
          <SavedViewTabs />
        </div>
        <ViewToggle value={listingsLayout} onChange={setListingsLayout} />
      </div>

      {/* The cut on top of the tab, and the named views that keep one. The
          "of" number is the whole tab AS THE MARKETPLACE CONTROL LEFT IT
          (marketCounts), not the whole store: those are the counts the tab
          badges show, so nothing on screen can disagree about how much this
          filter took away. The where-it-lives control rides this row's
          right end, so every narrowing control is on one line. */}
      <ListingFilters shown={items.length} hasListings={listingsState.items.length > 0}
        total={view.kind === "unavailable" ? null : marketCounts[marketId]}
        trailing={marketControl}
        storeOptions={storeOptions} ebayOptions={ebayOptions} showStore={showStore}
        saleEvents={saleEvents.items} />

      {/* The live listings' bulk bar (LiveBulkBar): arrives with the first
          tick on a live listing and leaves with the last. The few actions a
          seller reaches for every week are on it; the rest are under More. */}
      {selectedLive.length > 0 && (
        <LiveBulkBar
          count={selectedLive.length} total={liveItems.length}
          allSelected={allLiveSelected} onToggleAll={toggleAllLive} onClear={clearLive}
          filtered={!isEmptyFilters(listingFilters) || !!q}
          running={running} busy={!!running}
          actions={[
            { key: "relist", place: "bar", primary: true,
              icon: <RefreshCcw aria-hidden />,
              label: `End & relist (${selectedLive.length})`,
              onClick: endAndRelist, loading: running?.verb === "Relisting",
              disabled: !ebay.connected || !!running, title: needsEbay },
            { key: "lower", place: "bar", icon: <TrendingDown aria-hidden />,
              label: "Lower prices…", onClick: () => togglePanel("lower"),
              expanded: openPanel === "lower", loading: running?.verb === "Lowering",
              disabled: !ebay.connected || !!running, title: needsEbay },
            { key: "discount", place: "bar", icon: <BadgePercent aria-hidden />,
              label: "Apply discount…", onClick: () => togglePanel("discount"),
              expanded: openPanel === "discount",
              loading: running?.verb === "Starting the sale",
              disabled: !ebay.connected || !!running, title: needsEbay
                || "A sale eBay runs for a few days, with your price struck through." },
            { key: "add-sale", place: "bar-wide", icon: <Tag aria-hidden />,
              label: "Add to sale…", onClick: () => togglePanel("add-sale"),
              expanded: openPanel === "add-sale",
              loading: running?.verb === "Adding to the sale",
              disabled: !ebay.connected || !!running, title: needsEbay },
            etsy && { key: "crosspost", place: "bar-wide",
              icon: <ArrowRightLeft aria-hidden />,
              label: "Crosspost to Etsy…",
              barLabel: `Crosspost to Etsy (${selectedLive.length})`,
              onClick: () => setCrosspostOpen(true),
              disabled: !etsy.connected || !!running,
              title: etsy.connected ? undefined
                : etsy.access_pending
                  ? etsy.access_pending_note
                  : "Connect Etsy under Settings → Cross-posting marketplaces first." },
            { key: "remove-sale", place: "more", icon: <BadgeX aria-hidden />,
              label: inAnySale.length
                ? `Remove from sale (${inAnySale.length})` : "Remove from sale",
              onClick: removeFromSale,
              disabled: !ebay.connected || !!running || !inAnySale.length,
              title: inAnySale.length ? undefined : "None of these is in a sale." },
            { key: "shelf", place: "more", icon: <FolderInput aria-hidden />,
              label: "Move to store category…", onClick: () => togglePanel("shelf"),
              disabled: !ebay.connected || !!running, title: needsEbay },
            { key: "offers", place: "more", icon: <HandCoins aria-hidden />,
              label: "Send offers to watchers…", onClick: () => togglePanel("offers"),
              disabled: !ebay.connected || !!running, title: needsEbay },
            { key: "end", place: "more", icon: <CircleStop aria-hidden />,
              label: "End listings", danger: true, divider: true,
              onClick: endListings, disabled: !ebay.connected || !!running,
              title: needsEbay },
          ]}
          panel={running ? null : openPanel === "lower" ? (
            <BulkAmountPanel key="lower" amount={LOWER_PRICES} count={selectedLive.length}
              onSubmit={lowerPrices} onCancel={() => setOpenPanel(null)} />
          ) : openPanel === "offers" ? (
            <BulkAmountPanel key="offers" amount={SEND_OFFERS} count={selectedLive.length}
              onSubmit={sendOffers} onCancel={() => setOpenPanel(null)} />
          ) : openPanel === "discount" ? (
            <DiscountPanel key="discount" count={selectedLive.length}
              inOtherSales={inEditableSale.length} limits={saleEvents.limits}
              onSubmit={applyDiscount} onCancel={() => setOpenPanel(null)} />
          ) : openPanel === "add-sale" ? (
            <AddToSalePanel key="add-sale" count={selectedLive.length}
              sales={saleEvents.items} inOtherSales={inEditableSale.length}
              onSubmit={addToSale} onCancel={() => setOpenPanel(null)}
              onNew={() => { setOpenPanel(null); setSaleDialog(true); }} />
          ) : openPanel === "shelf" ? (
            <StoreCategoryPanel key="shelf" count={selectedLive.length}
              onSubmit={moveToShelf} onCancel={() => setOpenPanel(null)} />
          ) : null}
        />
      )}
      {saleDialog && (
        <SaleDialog open onClose={() => setSaleDialog(false)} ticked={selectedLive}
          shelves={storeCats.categories} activeCount={liveItems.length}
          limits={saleEvents.limits} onSubmit={createSale} />
      )}
      {crosspostOpen && (
        <Suspense fallback={null}>
          <CrosspostWizard items={selectedLive} onClose={() => setCrosspostOpen(false)} />
        </Suspense>
      )}

      {profit && (
        <p className="text-[13px] text-ink-secondary flex items-center gap-1.5">
          <strong className={profit.total >= 0 ? "text-success" : "text-warning"}
            title="What each item sold for minus what you paid, before fees & shipping — only items with a 'You paid' amount count. Where eBay hasn't reported the sale amount, the asking price stands in.">
            {profit.total >= 0 ? "+" : "−"}{formatMoney(Math.abs(profit.total), profit.currency)} profit
          </strong>
          <span>· {profit.count} item{profit.count === 1 ? "" : "s"}</span>
          {profit.approx > 0 && (
            <span>· {profit.approx} at the asking price</span>
          )}
        </p>
      )}

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

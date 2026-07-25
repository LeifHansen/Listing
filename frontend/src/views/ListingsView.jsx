import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { PlusCircle, Store, LogIn, CheckSquare, Trash2, X } from "lucide-react";
import { postJson } from "@/lib/api";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ListingCard } from "@/components/ListingCard";
import { ListingCardSkeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  BoxIllustration, TagIllustration, ClipboardIllustration,
} from "@/components/ui/illustrations";

/* One view powers Inventory / Drafts / Listings — same grid, different
   filter + empty state. */

const CONFIGS = {
  inventory: {
    title: "Inventory",
    sub: "Finds from Shop Mode waiting to be listed",
    filter: (i) => i.status === "unlisted",
    illustration: BoxIllustration,
    emptyTitle: "No unlisted finds",
    emptyMessage: "Scan items in Shop Mode while you're out hunting — tap Buy and they land here to finish later.",
    emptyAction: { label: "Open Shop Mode", icon: Store, view: "shop" },
  },
  drafts: {
    title: "Drafts",
    sub: "Works in progress and ended listings — open one to finish, publish, or relist",
    filter: (i) => i.status === "draft" || i.status === "dry_run" || i.status === "ended",
    illustration: ClipboardIllustration,
    emptyTitle: "No drafts",
    emptyMessage: "Drafts save automatically while you build a listing, so you can pick up right where you left off.",
    emptyAction: { label: "Create Listing", icon: PlusCircle, view: "new" },
  },
  listings: {
    title: "Listings",
    sub: "Everything you've created with Thryft Shop",
    filter: () => true,
    illustration: TagIllustration,
    emptyTitle: "No listings yet",
    emptyMessage: "Let's create your first listing — snap a few photos and the AI writes the rest.",
    emptyAction: { label: "Create Listing", icon: PlusCircle, view: "new" },
  },
};

export function ListingsView({ kind, search = "" }) {
  const cfg = CONFIGS[kind];
  const {
    listingsState, openListing, setView, startNew, user, openAuth, deleteListing,
    bulkDeleteListings, ebay, loadListings, metricsById,
  } = useApp();
  const { confirm } = useToast();

  // Select mode (Drafts): tap cards to select, then delete them all at once.
  const canSelect = kind === "drafts";
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

  // Reconcile Live statuses with eBay once per visit: anything sold or ended
  // on eBay's side flips to Ended here instead of showing Live forever.
  const synced = useRef(false);
  useEffect(() => {
    if (synced.current || kind !== "listings" || !user || !ebay.connected) return;
    synced.current = true;
    postJson("/api/ebay/sync-listings", {})
      .then((r) => { if (r.changed) loadListings({ quiet: true }); })
      .catch(() => {});
  }, [kind, user, ebay.connected, loadListings]);

  const askDelete = async (item) => {
    const name = item.listing?.title || item.title || "this listing";
    if (await confirm({
      title: "Delete this listing?",
      message: `"${name}" will be permanently removed. This can't be undone.`,
      confirmLabel: "Delete",
      danger: true,
    })) deleteListing(item.id);
  };

  const q = search.trim().toLowerCase();
  const items = listingsState.items
    .filter(cfg.filter)
    .filter((i) => !q
      || (i.listing?.title || i.title || "").toLowerCase().includes(q)
      || (i.listing?.brand || "").toLowerCase().includes(q));

  const go = () => (cfg.emptyAction.view === "new" ? startNew() : setView(cfg.emptyAction.view));

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
          illustration={cfg.illustration}
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
          illustration={cfg.illustration}
          title={q ? "No matches" : cfg.emptyTitle}
          message={q ? `Nothing matches "${search}" here.` : cfg.emptyMessage}
          action={!q && (
            <Button variant="primary" size="lg" onClick={go}>
              <cfg.emptyAction.icon aria-hidden /> {cfg.emptyAction.label}
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

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-ink">{cfg.title}</h1>
          <p className="text-sm text-ink-secondary mt-1">{cfg.sub}</p>
        </div>
        {canSelect && hasItems && (
          <div className="flex items-center gap-2">
            {selecting ? (
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
            )}
          </div>
        )}
      </div>
      {body}
    </div>
  );
}

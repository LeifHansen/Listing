import { useState } from "react";
import { motion } from "framer-motion";
import { PlusCircle, Store, LogIn } from "lucide-react";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ListingCard } from "@/components/ListingCard";
import { LiveListingEditor } from "@/components/LiveListingEditor";
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
    sub: "Works in progress — open one to finish and publish",
    filter: (i) => i.status === "draft" || i.status === "dry_run",
    illustration: ClipboardIllustration,
    emptyTitle: "No drafts",
    emptyMessage: "Drafts save automatically while you build a listing, so you can pick up right where you left off.",
    emptyAction: { label: "Create Listing", icon: PlusCircle, view: "new" },
  },
  listings: {
    title: "Listings",
    sub: "Everything you've created with Thryft",
    filter: () => true,
    illustration: TagIllustration,
    emptyTitle: "No listings yet",
    emptyMessage: "Let's create your first listing — snap a few photos and the AI writes the rest.",
    emptyAction: { label: "Create Listing", icon: PlusCircle, view: "new" },
  },
};

export function ListingsView({ kind, search = "" }) {
  const cfg = CONFIGS[kind];
  const { listingsState, loadListings, openListing, setView, startNew, user, openAuth,
    ebayListings, syncEbay, endEbayListing, removeListing } = useApp();
  const { toast, confirm } = useToast();
  const [editingLive, setEditingLive] = useState(null);

  const onEndLive = async (item) => {
    if (!(await confirm({
      title: "End this eBay listing?",
      message: "This ends the live listing on eBay. This can't be undone.",
      confirmLabel: "End listing",
      danger: true,
    }))) return;
    try {
      const r = await endEbayListing(item.ebay_item_id);
      toast(r?.message || "Listing ended on eBay.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't end listing: ${e.message}`, { kind: "error" });
    }
  };

  const onDelete = async (item) => {
    const live = item.status === "published" || item.status === "live";
    if (!(await confirm({
      title: "Delete this listing?",
      message: live
        ? "This will also end the live listing on eBay. This can't be undone."
        : "This removes it from Thryft. This can't be undone.",
      confirmLabel: "Delete",
      danger: true,
    }))) return;
    try {
      await removeListing(item.id);
      toast("Listing deleted.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't delete: ${e.message}`, { kind: "error" });
    }
  };

  const q = search.trim().toLowerCase();
  // On the Listings tab, fold in live listings pulled from eBay (when the
  // user enabled Sync in Settings); other tabs stay QuickFlip-only.
  const base = kind === "listings"
    ? [...listingsState.items, ...(ebayListings || [])]
    : listingsState.items;
  const items = base
    .filter(cfg.filter)
    .filter((i) => !q
      || (i.listing?.title || i.title || "").toLowerCase().includes(q)
      || (i.listing?.brand || "").toLowerCase().includes(q));

  const go = () => (cfg.emptyAction.view === "new" ? startNew() : setView(cfg.emptyAction.view));

  let body;
  // Gate on loaded alone: every load is quiet, so `loading` never went true
  // and users saw a flash of "No listings yet" instead of the skeletons.
  if (!listingsState.loaded) {
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
            <ListingCard item={item} onOpen={openListing} onDelete={onDelete}
              onEditLive={setEditingLive} onEndLive={onEndLive} />
          </motion.div>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-ink">{cfg.title}</h1>
        <p className="text-sm text-ink-secondary mt-1">
          {kind === "listings" && user
            ? (syncEbay
                ? "Managing all your active eBay listings — edit price, quantity, or end a listing."
                : cfg.sub)
            : cfg.sub}
          {kind === "listings" && user && !syncEbay && (
            <>
              {" "}
              <button
                type="button"
                onClick={() => setView("settings")}
                className="font-semibold text-blue hover:underline"
              >
                Sync all eBay listings
              </button>
              {" in Settings to manage your full inventory here."}
            </>
          )}
        </p>
      </div>
      {body}
      {/* key remounts the editor per listing so its price/qty fields
          initialize from the opened item (useState inits run once per mount) —
          otherwise stale values from a previous edit get pushed to eBay. */}
      <LiveListingEditor
        key={editingLive?.id || "none"}
        item={editingLive}
        onClose={() => setEditingLive(null)}
      />
    </div>
  );
}

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BadgePercent, ChevronDown, Plus, RefreshCw } from "lucide-react";
import { postJson } from "@/lib/api";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ListingCardSkeleton } from "@/components/ui/Skeleton";
import { effectiveState, groupSaleEvents } from "@/lib/saleEvents";
import { isLive } from "@/lib/listingsView";
import { useStoreCategories } from "@/lib/useStoreCategories";
import { bulkToast } from "@/lib/bulkActions";
import { SaleCard } from "./SaleCard";
import { SaleDialog } from "./SaleDialog";

function SaleIllustration({ className }) {
  return (
    <div className={`grid place-items-center size-16 rounded-full bg-blue-soft text-blue ${className || ""}`}>
      <BadgePercent size={30} aria-hidden />
    </div>
  );
}

/* The Sales half of the Manage tab: the seller's eBay sale events, made here
 * or in Seller Hub, as cards in three groups — running, still to come, and
 * ended (the last 30 days, folded away).
 *
 * Simpler than Seller Hub on purpose. A sale here is one percentage off a set
 * of listings for a set of days — no coupons, no codes, no tiers. A sale set
 * up in Seller Hub with more than that is shown, marked "Managed in Seller
 * Hub" with the reason, and changed there. */
export function SalesView() {
  const {
    ebay, saleEvents, loadSaleEvents, adoptSaleEvents, listingsState, liveSelection,
    setListingFilters, setListingsTab, setManageSection, openSettings,
  } = useApp();
  // Fresh from eBay when the section opens, unless this screen asked in the
  // last few minutes (the store keeps that window).
  useEffect(() => { if (ebay.connected) loadSaleEvents(); }, [ebay.connected, loadSaleEvents]);
  const { toast, confirm } = useToast();
  const { categories: shelves } = useStoreCategories();
  const [dialog, setDialog] = useState(null);       // null | { event? }
  const [busy, setBusy] = useState(false);
  const [showEnded, setShowEnded] = useState(false);

  const groups = useMemo(() => groupSaleEvents(saleEvents.items), [saleEvents.items]);
  const live = listingsState.items.filter(isLive);
  const ticked = live.filter((i) => liveSelection[i.id]);
  const limits = saleEvents.limits;

  if (!ebay.connected) {
    return (
      <Card className="p-0">
        <EmptyState illustration={SaleIllustration} title="Connect eBay to run sales"
          message="Sales are run by eBay — a percentage off a set of your listings for a few days, with the old price struck through for every buyer."
          action={<Button variant="primary" onClick={() => openSettings("ebay-account")}>Connect eBay</Button>} />
      </Card>
    );
  }

  const viewListings = (event) => {
    setListingsTab("all");
    setListingFilters({ sale: `event:${event.id}` });
    setManageSection("listings");
  };

  const create = async (body) => {
    try {
      const res = await postJson("/api/sale-events", body);
      adoptSaleEvents(res);
      const report = res.report || {};
      if (res.event) {
        const [msg, opts] = bulkToast(report, {
          did: (n) => `“${res.event.name}” is ${effectiveState(res.event) === "scheduled" ? "scheduled" : "on"} — ${n} listing${n === 1 ? "" : "s"} at ${res.event.percent_off}% off`,
        });
        toast(msg, opts);
      } else {
        toast(...bulkToast(report, { did: () => "", empty: "None of those could go in a sale." }));
        return false;
      }
      return true;
    } catch (e) {
      toast(`Couldn't start the sale: ${e.message}`, { kind: "error" });
      return false;
    }
  };

  const edit = (event) => async (body) => {
    try {
      const res = await postJson(`/api/sale-events/${encodeURIComponent(event.id)}`, body,
        { method: "PATCH" });
      adoptSaleEvents(res);
      toast(`Saved “${res.event?.name || event.name}”.`, { kind: "success" });
      return true;
    } catch (e) {
      toast(`Couldn't change the sale: ${e.message}`, { kind: "error" });
      return false;
    }
  };

  const end = async (event) => {
    const running = effectiveState(event) === "running";
    if (!(await confirm({
      title: running ? `End “${event.name}” now?` : `Cancel “${event.name}”?`,
      message: running
        ? `Its ${event.count ?? ""} listing${event.count === 1 ? " goes" : "s go"} back to full price on eBay straight away.`
        : "It won't start. Your listings stay at their full price.",
      confirmLabel: running ? "End sale" : "Cancel sale",
      danger: true,
    }))) return;
    setBusy(true);
    try {
      const res = await postJson(`/api/sale-events/${encodeURIComponent(event.id)}/end`, {});
      adoptSaleEvents(res);
      toast(running ? `Ended “${event.name}”.` : `Cancelled “${event.name}”.`, { kind: "success" });
    } catch (e) {
      toast(`Couldn't end the sale: ${e.message}`, { kind: "error" });
    } finally {
      setBusy(false);
    }
  };

  const grid = (events) => (
    <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-3">
      {events.map((e) => (
        <SaleCard key={e.id} event={e} busy={busy} onViewListings={viewListings}
          onEdit={(ev) => setDialog({ event: ev })} onEnd={end} />
      ))}
    </div>
  );

  const nothing = saleEvents.loaded && !saleEvents.items.length;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-[13px] text-ink-secondary min-w-0 flex-1">
          A percentage off, for a few days — eBay shows your old price struck
          through and ends it by itself. Sales you set up in Seller Hub show here too.
        </p>
        <Button variant="ghost" size="sm" onClick={() => loadSaleEvents({ force: true })}
          loading={saleEvents.loading} aria-label="Check eBay for sales">
          <RefreshCw aria-hidden /> <span className="hidden sm:inline">Refresh</span>
        </Button>
        <Button variant="primary" size="sm" onClick={() => setDialog({})}>
          <Plus aria-hidden /> New sale
        </Button>
      </div>

      {saleEvents.needsReconnect && (
        <p className="text-sm rounded-tile border border-warning/30 bg-warning-soft p-3 text-ink flex items-start gap-2">
          <AlertTriangle size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
          <span>
            Your eBay connection was made before sales were possible here.
            Reconnect eBay under Settings once, and your sales will show.
          </span>
        </p>
      )}
      {!saleEvents.checked && !saleEvents.needsReconnect && saleEvents.loaded && (
        <p className="text-[13px] text-ink-secondary flex items-center gap-1.5">
          <AlertTriangle size={14} className="text-warning" aria-hidden />
          We couldn’t check eBay just now — this is what we last knew.
        </p>
      )}

      {!saleEvents.loaded && saleEvents.loading && (
        <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-3">
          {[0, 1, 2].map((i) => <ListingCardSkeleton key={i} />)}
        </div>
      )}

      {nothing && (
        <Card className="p-0">
          <EmptyState illustration={SaleIllustration}
            title="No sales yet"
            message="Start one here, or tick listings on the Listings side and press Apply discount."
            action={<Button variant="primary" onClick={() => setDialog({})}><Plus aria-hidden /> New sale</Button>} />
        </Card>
      )}

      {groups.running.length > 0 && (
        <section className="flex flex-col gap-2.5" aria-label="Running">
          <h2 className="text-[13px] font-bold uppercase tracking-wide text-ink-secondary">
            Running · {groups.running.length}
          </h2>
          {grid(groups.running)}
        </section>
      )}
      {groups.scheduled.length > 0 && (
        <section className="flex flex-col gap-2.5" aria-label="Coming up">
          <h2 className="text-[13px] font-bold uppercase tracking-wide text-ink-secondary">
            Coming up · {groups.scheduled.length}
          </h2>
          {grid(groups.scheduled)}
        </section>
      )}
      {groups.ended.length > 0 && (
        <section className="flex flex-col gap-2.5" aria-label="Ended">
          <button type="button" onClick={() => setShowEnded((s) => !s)} aria-expanded={showEnded}
            className="self-start inline-flex items-center gap-1 text-[13px] font-bold uppercase tracking-wide text-ink-secondary hover:text-ink cursor-pointer">
            Ended · {groups.ended.length}
            <ChevronDown size={14} aria-hidden className={showEnded ? "rotate-180" : undefined} />
          </button>
          {showEnded && grid(groups.ended)}
        </section>
      )}

      {dialog && (
        <SaleDialog open onClose={() => setDialog(null)} event={dialog.event || null}
          ticked={ticked} shelves={shelves} activeCount={live.length} limits={limits}
          onSubmit={dialog.event ? edit(dialog.event) : create} />
      )}
    </div>
  );
}

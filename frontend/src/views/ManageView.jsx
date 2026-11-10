import { useState } from "react";
import { Download, RefreshCw, Tags } from "lucide-react";
import { motion } from "framer-motion";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { exportListingsCsv } from "@/lib/listingExport";
import { ListingsView } from "./ListingsView";
import { Workflow } from "./NewListing";

/* The Manage screen: everything the seller already has, and nothing about
   making a new one. It is the second half of what the old Sell tab was --
   the listings manager, which used to live below an upload box and a drafts
   grid and so started halfway down a page nobody had scrolled.

   Thin on purpose. The manager owns its own tabs, filters and controls
   (ListingsView); this adds the page header — with the two actions that are
   about the whole store rather than the open tab, in its action slot — and
   the one rule the split needed: an open listing renders HERE rather than
   sending the seller to the List tab. Tapping a live listing on Manage and
   being dropped onto the photo uploader would be a worse answer than the
   merged screen ever gave. */
export function ManageView({ search = "" }) {
  const { session } = useApp();

  // Same gate NewListing uses, for the same reason: a session IS the editor.
  // Both tabs render it so a listing opens, and closes, where you already are.
  if (session) return <Workflow />;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      className="flex flex-col gap-4"
    >
      <PageHeader icon={Tags} title="Manage" action={<ManageActions />} />
      <ListingsView search={search} />
    </motion.div>
  );
}

/* The whole-store actions, in the header where whole-page controls go. They
   used to have a row of their own above the tabs, right-aligned, floating
   away from the thing they were about. Both ignore the open tab — the sync
   reads the seller's eBay store and the export writes every listing on the
   account — which is exactly why they belong up here and not beside the tab
   pills. */
function ManageActions() {
  const { user, ebay, listingsState, storeSync, syncStore } = useApp();
  const { toast } = useToast();
  const [exporting, setExporting] = useState(false);

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

  // Download the whole store as a spreadsheet — every listing on the account,
  // with a link to every photo. Deliberately NOT the open tab and not the
  // page the grid has loaded: the file is a backup, an inventory count, the
  // thing an accountant asks for, and all three want everything.
  const exportCsv = async () => {
    setExporting(true);
    try {
      const { how, filename, total, exported } = await exportListingsCsv();
      // The seller dismissed the iOS share sheet. That is a decision, not a
      // failure, and saying "exported" about a file they declined to keep
      // would be telling them something that did not happen.
      if (how === null) return;
      if (exported != null && total != null && exported < total) {
        toast(
          `Exported the first ${exported} of ${total} listings to ${filename} — `
          + "that's as much as one download carries. Get in touch if you need "
          + "the rest.",
          { kind: "warning" });
        return;
      }
      toast(
        total != null
          ? `Exported ${total} listing${total === 1 ? "" : "s"} to ${filename}.`
          : `Exported your listings to ${filename}.`,
        { kind: "success" });
    } catch (e) {
      toast(`Couldn't export your listings: ${e.message}`, { kind: "error" });
    } finally {
      setExporting(false);
    }
  };

  if (!user) return null;
  return (
    <>
      {ebay.connected && (
        <Button variant="soft" size="sm" onClick={importFromEbay} loading={storeSync.syncing}>
          <RefreshCw aria-hidden /> Sync with eBay
        </Button>
      )}
      {/* Offered once there is something to put in the file. Keyed off the
          whole store rather than the open tab: the export ignores the tabs,
          so a seller on an empty Inactive tab must not be told there is
          nothing to export when their store is full. */}
      {listingsState.items.length > 0 && (
        <Button variant="ghost" size="iconSm" onClick={exportCsv} loading={exporting}
          aria-label="Export CSV"
          title="Download every listing on your account as a CSV spreadsheet">
          {!exporting && <Download aria-hidden />}
        </Button>
      )}
    </>
  );
}

import { useState } from "react";
import { AlertTriangle, Unlink } from "lucide-react";
import { postJson } from "@/lib/api";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toaster";

/* Listings left over from a previously-connected eBay account.
 *
 * Connecting a second eBay account doesn't move the first account's listings
 * anywhere — records belong to the APP user, not the eBay one — so they stay
 * in the app looking exactly like listings of the account now connected. They
 * are excluded from every eBay call (see services/listing_sync.belongs_to),
 * but "excluded" is invisible; without this notice they simply read as "the
 * new account somehow has my old items". */
export function ForeignListingsNotice() {
  const { ebay, loadEbayStatus, loadListings } = useApp();
  const { toast, confirm } = useToast();
  const [working, setWorking] = useState(false);
  const foreign = ebay.foreign_listings || 0;
  // eBay-linked records from before the app tracked which account listed
  // them. After a switch these are the OLD account's items wearing no label —
  // but a seller who never switched has the same shape for their own older
  // imports, so nothing but the seller can say whose they are. That's why
  // their unlink rides the same button but only when they exist, and the
  // request says so explicitly (include_unowned).
  const unowned = ebay.unowned_listings || 0;
  const count = foreign + unowned;
  if (!count) return null;

  const release = async () => {
    const ok = await confirm({
      title: `Unlink ${count} listing${count === 1 ? "" : "s"} from your old eBay account?`,
      message: "They stay here with their photos and details, as drafts you can "
        + "publish to the account you're connected to now. Nothing is deleted, "
        + "and nothing changes on eBay."
        + (unowned
          ? ` ${unowned} of them ${unowned === 1 ? "was" : "were"} linked before `
            + "the app tracked accounts — only unlink if those items aren't "
            + "on the account you're connected to now."
          : ""),
      confirmLabel: "Unlink them",
    });
    if (!ok) return;
    setWorking(true);
    try {
      const res = await postJson("/api/ebay/release-foreign-listings",
        unowned ? { include_unowned: true } : {});
      // Each unlink is its own write, so one pass is bounded. A bounded run
      // that reads like a finished one is the thing to avoid: the banner
      // above would simply come back with a smaller number and no reason.
      const left = res.remaining || 0;
      toast(
        `Unlinked ${res.released} listing${res.released === 1 ? "" : "s"}.`
        + (left ? ` ${left} still to go — press it again to carry on.` : ""),
        { kind: left ? "warning" : "success" });
      await Promise.all([loadEbayStatus(), loadListings()]);
    } catch (e) {
      toast(`Couldn't unlink them: ${e.message}`, { kind: "error" });
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="rounded-tile bg-warning-soft border border-warning/30 p-4 flex gap-3">
      <AlertTriangle size={18} className="text-warning shrink-0 mt-0.5" aria-hidden />
      <div className="text-sm min-w-0">
        <p className="font-bold text-ink">
          {count} listing{count === 1 ? "" : "s"} here {count === 1 ? "is" : "are"} linked
          to an eBay account that isn&apos;t the one connected
        </p>
        <p className="text-ink-secondary mt-0.5">
          They were listed on an account you connected earlier. Thryft Shop
          leaves them alone — it won’t sync, edit, or end them while
          {ebay.username ? <strong className="text-ink"> {ebay.username} </strong> : " this account "}
          is connected. Reconnect that account to manage them again, or unlink
          them here to keep the drafts and drop the old eBay link.
        </p>
        <Button variant="secondary" className="mt-2.5" onClick={release} loading={working}>
          <Unlink aria-hidden /> Unlink from the old account
        </Button>
      </div>
    </div>
  );
}

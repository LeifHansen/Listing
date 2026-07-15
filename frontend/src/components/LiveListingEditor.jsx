import { useState } from "react";
import { Dialog } from "@/components/ui/Dialog";
import { Field, Input } from "@/components/ui/fields";
import { Button } from "@/components/ui/Button";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";

/* Inline editor for a live eBay listing — change price and/or quantity and
   push the update straight to eBay (Trading API ReviseInventoryStatus). */
export function LiveListingEditor({ item, onClose }) {
  const { reviseEbayListing } = useApp();
  const { toast } = useToast();
  const l = item?.listing || {};
  const [price, setPrice] = useState(l.price ?? "");
  const [quantity, setQuantity] = useState(l.quantity ?? "");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    const payload = {};
    if (price !== "" && Number(price) !== l.price) payload.price = Number(price);
    if (quantity !== "" && Number(quantity) !== l.quantity) payload.quantity = Number(quantity);
    if (Object.keys(payload).length === 0) { onClose(); return; }
    setSaving(true);
    try {
      const r = await reviseEbayListing(item.ebay_item_id, payload);
      toast(r?.message || "Listing updated on eBay.", { kind: "success" });
      onClose();
    } catch (e) {
      toast(`Couldn't update: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={!!item} onClose={onClose} title="Edit live listing">
      {l.title && (
        <p className="text-sm text-ink-secondary mb-4 line-clamp-2">{l.title}</p>
      )}
      <div className="grid grid-cols-2 gap-3">
        <Field label="Price">
          <Input
            type="number" min="0" step="0.01" inputMode="decimal"
            value={price} onChange={(e) => setPrice(e.target.value)}
            autoFocus
          />
        </Field>
        <Field label="Quantity">
          <Input
            type="number" min="0" step="1" inputMode="numeric"
            value={quantity} onChange={(e) => setQuantity(e.target.value)}
          />
        </Field>
      </div>
      <p className="text-xs text-ink-faint mt-3">
        Changes are saved directly to your live eBay listing.
      </p>
      <div className="flex justify-end gap-2 mt-6">
        <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
        <Button variant="primary" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save to eBay"}
        </Button>
      </div>
    </Dialog>
  );
}

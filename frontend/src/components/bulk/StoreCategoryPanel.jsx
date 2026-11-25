import { useState } from "react";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/fields";
import { useStoreCategories } from "@/lib/useStoreCategories";

/* "Move N listings to [ Vintage Tees ▾ ]" — the shelf a bulk move needs,
 * under the selection bar like the other bulk amounts.
 *
 * Real shelves only: an eBay revise has no way to send a listing back to the
 * store's top level (an empty StoreCategoryID is read as "leave it", and 0
 * means "top level" only on a create), so the choice is never offered. */
export function StoreCategoryPanel({ count, busy, onSubmit, onCancel }) {
  const { categories, checked, store } = useStoreCategories();
  const [id, setId] = useState("");
  const shelf = categories.find((c) => String(c.id) === id);
  const ready = !!shelf;

  let body;
  if (checked === undefined) {
    body = <p className="text-[13px] text-ink-secondary">Reading your eBay Store’s categories…</p>;
  } else if (checked === false) {
    body = (
      <p className="text-[13px] text-ink-secondary">
        We couldn’t read your eBay Store’s categories just now — try again in a moment.
      </p>
    );
  } else if (!store || !categories.length) {
    body = (
      <p className="text-[13px] text-ink-secondary">
        Store categories are the shelves of an eBay Store, and this account
        doesn’t have one — there’s nowhere to move these to.
      </p>
    );
  } else {
    body = (
      <label className="flex flex-wrap items-center gap-2 text-[13px] font-semibold text-ink">
        Move {count === 1 ? "it" : `all ${count}`} to
        <Select aria-label="Store category" value={id} autoFocus
          onChange={(e) => setId(e.target.value)}
          className="h-9 w-64 max-w-full text-sm">
          <option value="" disabled>Pick a store category…</option>
          {categories.map((c) => (
            <option key={c.id} value={String(c.id)}>
              {c.level > 1 ? `${" ".repeat(c.level - 1)}${c.name}` : c.name}
            </option>
          ))}
        </Select>
      </label>
    );
  }

  return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      className="overflow-hidden"
    >
      <div className="rounded-[13px] border border-line bg-bg-sunken p-3.5">
        {body}
        <p className="mt-2 text-[12px] text-ink-secondary">
          Each one moves on eBay too — only its store shelf changes.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button variant="primary" size="sm" loading={busy}
            disabled={busy || !ready} onClick={() => onSubmit(shelf)}>
            {ready ? `Move ${count} to “${shelf.name}”` : `Move ${count}`}
          </Button>
          <Button variant="ghost" size="sm" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </div>
    </motion.div>
  );
}

import { useState } from "react";
import { FolderTree, Pencil } from "lucide-react";
import { patchJson, postJson } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { CategorySuggestList } from "./CategorySuggestList";

/* Compact category display + changer for draft cards (the drafts grid, the
   dashboard's recent strip, the listings manager). A wrong AI category pick
   is easy to miss on a card that only shows
   title and price, and expensive once published — so the category sits on
   the card face, one tap from being fixed. Suggestions come from the same
   /api/category-suggestions endpoint as the editor's picker; choosing one
   hands {category_id, category_suggestion} to the parent, which owns
   persistence. */
export function CategoryQuickPick({ listing, onPick, saving }) {
  const l = listing || {};
  const [open, setOpen] = useState(false);
  const [sugg, setSugg] = useState(null); // null | {loading} | {items} | {error}

  const path = (l.category_suggestion || "").trim();
  const id = String(l.category_id ?? "").trim();
  // The leaf name reads better than a full "A > B > C" path at card width.
  const leaf = path.split(">").map((s) => s.trim()).filter(Boolean).pop() || "";
  const label = leaf || (id ? `Category #${id}` : "No category — tap to pick");
  const missing = !id;

  const load = async () => {
    setOpen(true);
    setSugg({ loading: true });
    // Brand + title only — NOT the saved category text: that text is exactly
    // what's in doubt when someone reaches for this control, and feeding it
    // back in steers the search toward the same wrong branch.
    const query = [l.brand, l.title].filter(Boolean).join(" ").trim() || path;
    if (!query) {
      setSugg({ error: "Add a title first — category matches run on it." });
      return;
    }
    try {
      const res = await postJson("/api/category-suggestions", { query, limit: 5 });
      setSugg({ items: res.suggestions || [] });
    } catch (e) {
      setSugg({ error: `Couldn't fetch categories: ${e.message}` });
    }
  };

  const choose = (c) => {
    setOpen(false);
    onPick({
      category_id: String(c.category_id),
      category_suggestion: c.path || c.category_name,
    });
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={load}
        disabled={saving}
        aria-label={`Category: ${label} — change`}
        title={(path || label) + (id ? ` (#${id})` : "") + " — tap to change"}
        className={cn(
          "w-full flex items-center gap-1.5 text-left text-[13px] rounded-input",
          "px-1 py-1 cursor-pointer transition-colors duration-150",
          "hover:bg-bg-sunken disabled:opacity-60",
          missing ? "text-warning font-semibold" : "text-ink-secondary",
        )}
      >
        <FolderTree size={14} aria-hidden
          className={cn("shrink-0", missing ? "text-warning" : "text-ink-faint")} />
        <span className="min-w-0 truncate">{saving ? "Saving…" : label}</span>
        <Pencil size={12} className="ml-auto shrink-0 text-ink-faint" aria-hidden />
      </button>
    );
  }

  // The list itself is shared with the editor's Category section
  // (CategorySuggestList), so a category picked on a card looks exactly
  // like one picked in the editor.
  return (
    <CategorySuggestList
      sugg={sugg}
      currentId={id}
      onChoose={choose}
      onClose={() => setOpen(false)}
      emptyText="No matches — edit the title in Review & List and try again."
    />
  );
}


/* The same control, wired to a SAVED listing — display, pick, patch, refresh.

   A wrong category is the AI misfire that costs most once it is published,
   and it is invisible on a card that shows a title and a price. So it sits on
   the face of every draft card there is: the drafts grid (which is also how
   a bulk batch is reviewed), the dashboard's recent cards and the listings
   manager. One component, so they cannot drift.
*/
export function DraftCategoryEdit({ item, className }) {
  const { loadListings } = useApp();
  const { toast } = useToast();
  const [saving, setSaving] = useState(false);
  const save = async (patch) => {
    setSaving(true);
    try {
      // The patch names the two category fields and leaves everything else
      // as stored — a card must never write back a whole listing it only
      // holds a summary of.
      await patchJson(`/api/listings/${item.id}`, patch);
      // Refresh the cache so the card (and its Publish gate, and the
      // condition list that hangs off the category) sees the change.
      await loadListings({ quiet: true });
    } catch (e) {
      toast(`Couldn't save the category: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className={className}>
      <CategoryQuickPick listing={item.listing} onPick={save} saving={saving} />
    </div>
  );
}

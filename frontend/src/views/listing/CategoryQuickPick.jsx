import { useState } from "react";
import { FolderTree, Pencil, X } from "lucide-react";
import { postJson } from "@/lib/api";
import { cn } from "@/lib/utils";

/* Compact category display + changer for draft cards (drafts strip + bulk
   queue). A wrong AI category pick is easy to miss on a card that only shows
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

  return (
    <div className="flex flex-col gap-1.5 rounded-input border border-line bg-bg-sunken p-2">
      <div className="flex items-center gap-1.5 text-[12px] font-semibold text-ink-faint">
        <FolderTree size={13} aria-hidden />
        <span className="min-w-0 truncate">Pick the right category</span>
        <button type="button" onClick={() => setOpen(false)}
          aria-label="Close category picker"
          className="ml-auto shrink-0 grid place-items-center size-6 rounded-full cursor-pointer text-ink-faint hover:text-ink hover:bg-card">
          <X size={13} aria-hidden />
        </button>
      </div>
      {sugg?.loading && (
        <p className="text-[13px] text-ink-secondary px-1 py-1">Matching eBay categories…</p>
      )}
      {sugg?.error && (
        <p className="text-[13px] text-ink-secondary px-1 py-1">{sugg.error}</p>
      )}
      {sugg?.items && !sugg.items.length && (
        <p className="text-[13px] text-ink-secondary px-1 py-1">
          No matches — edit the title in Preview &amp; Edit and try again.
        </p>
      )}
      {sugg?.items?.map((c) => (
        <button
          key={c.category_id}
          type="button"
          onClick={() => choose(c)}
          className={cn(
            "w-full flex items-center justify-between gap-2 text-left px-2.5 py-2 rounded-input border text-[13px]",
            "transition-colors duration-150 cursor-pointer",
            String(c.category_id) === id
              ? "border-blue bg-blue-soft"
              : "border-line bg-card hover:border-line-strong",
          )}
        >
          <span className="min-w-0 text-ink leading-snug">{c.path || c.category_name}</span>
          <span className="shrink-0 font-bold text-blue tabular-nums text-[12px]">
            #{c.category_id}
          </span>
        </button>
      ))}
    </div>
  );
}

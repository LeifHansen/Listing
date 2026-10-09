import { FolderTree, X } from "lucide-react";
import { cn } from "@/lib/utils";

/* The eBay category picker, as a list: the matches for this listing, one
 * tap each, the chosen one marked.
 *
 * One list for every place a category is picked -- the draft cards
 * (CategoryQuickPick) and the editor's Category section -- because a
 * category chosen in one place must look like a category chosen in the
 * other. The caller owns the state: `sugg` is what the suggestion call said
 * (null | {loading} | {items} | {error}), `currentId` the listing's category,
 * and `onChoose` gets the suggestion that was tapped. There is no floating
 * layer: the list swaps in where the category line was and swaps out again
 * when one is chosen (or `onClose` is pressed), so nothing can sit over the
 * form. */
export function CategorySuggestList({
  sugg, currentId, onChoose, onClose, title = "Pick the right category",
  emptyText = "No matches — edit the title and try again.", children, className,
}) {
  const id = String(currentId ?? "").trim();
  return (
    <div
      role="group" aria-label={title}
      className={cn("flex flex-col gap-1.5 rounded-input border border-line bg-bg-sunken p-2", className)}
    >
      <div className="flex items-center gap-1.5 text-[12px] font-semibold text-ink-faint">
        <FolderTree size={13} aria-hidden />
        <span className="min-w-0 truncate">{title}</span>
        {onClose && (
          <button type="button" onClick={onClose}
            aria-label="Close category picker"
            className="ml-auto shrink-0 grid place-items-center size-6 rounded-full cursor-pointer text-ink-faint hover:text-ink hover:bg-card">
            <X size={13} aria-hidden />
          </button>
        )}
      </div>
      {children}
      {sugg?.loading && (
        <p className="text-[13px] text-ink-secondary px-1 py-1">Matching eBay categories…</p>
      )}
      {sugg?.error && (
        <p className="text-[13px] text-ink-secondary px-1 py-1">{sugg.error}</p>
      )}
      {sugg?.items && !sugg.items.length && (
        <p className="text-[13px] text-ink-secondary px-1 py-1">{emptyText}</p>
      )}
      {sugg?.items?.map((c) => (
        <button
          key={c.category_id}
          type="button"
          onClick={() => onChoose(c)}
          aria-pressed={String(c.category_id) === id}
          className={cn(
            "w-full flex items-center justify-between gap-2 text-left px-2.5 py-2 rounded-input border text-[13px]",
            "transition-colors duration-150 cursor-pointer",
            String(c.category_id) === id
              ? "border-blue bg-blue-soft"
              : "border-line bg-card hover:border-line-strong",
          )}
        >
          <span className="min-w-0 text-ink leading-snug">{c.path || c.category_name}</span>
          <span className="shrink-0 font-display font-bold text-blue tabular-nums text-[12px]">
            #{c.category_id}
          </span>
        </button>
      ))}
    </div>
  );
}

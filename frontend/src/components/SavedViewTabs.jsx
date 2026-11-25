import { useMemo } from "react";
import { Bookmark, Trash2 } from "lucide-react";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { matchingViewId } from "@/lib/listingFilters";
import { cn } from "@/lib/utils";

/* The seller's saved views, as tabs beside the lifecycle tabs.
 *
 * A saved view is a name for a tab plus filters (plus, now, an order), so it
 * sits where the tabs are, after a divider — the way Shopify's admin keeps
 * saved views in the index's tab strip. That leaves the filter row for the
 * controls that make a cut, and puts the cuts already made where a seller
 * looks to pick a list.
 *
 * A neutral pressed state — blue is the lifecycle tab's — and the delete only
 * on the view that is showing: a row of bookmarks each with its own bin read
 * as a row of things to be careful of. */
export function SavedViewTabs() {
  const {
    user, savedViews, listingsTab, listingFilters, listingsSort,
    applyListingView, deleteListingView,
  } = useApp();
  const { confirm } = useToast();
  const views = savedViews.items;
  const showingId = useMemo(
    () => matchingViewId(views, listingsTab, listingFilters, listingsSort),
    [views, listingsTab, listingFilters, listingsSort]);

  if (!user || !views.length) return null;

  const askDelete = async (view) => {
    if (await confirm({
      title: `Delete “${view.name}”?`,
      message: "The view is removed from every device. Your listings aren't "
        + "touched — a view is only a saved set of filters.",
      confirmLabel: "Delete view",
      danger: true,
    })) deleteListingView(view.id);
  };

  return (
    <>
      <span aria-hidden className="shrink-0 w-px h-6 bg-line mx-1" />
      {views.map((v) => {
        const showing = v.id === showingId;
        return (
          <span key={v.id} className={cn(
            "shrink-0 inline-flex items-center rounded-full border transition-colors duration-150",
            showing
              ? "bg-bg-sunken border-line-strong"
              : "bg-card border-line hover:border-line-strong",
          )}>
            <button
              type="button"
              onClick={() => applyListingView(v)}
              aria-pressed={showing}
              title={`Show “${v.name}”`}
              className={cn(
                "inline-flex items-center gap-1.5 h-9 pl-3 rounded-full",
                "text-[13px] font-semibold cursor-pointer max-w-52",
                showing ? "pr-1.5 rounded-r-none text-ink" : "pr-3.5 text-ink-secondary hover:text-ink",
              )}
            >
              <Bookmark size={13} aria-hidden className="shrink-0" />
              <span className="truncate">{v.name}</span>
            </button>
            {showing && (
              <button
                type="button"
                onClick={() => askDelete(v)}
                aria-label={`Delete the view “${v.name}”`}
                title={`Delete “${v.name}”`}
                className="grid place-items-center size-9 pr-1 rounded-r-full cursor-pointer text-ink-faint hover:text-error"
              >
                <Trash2 size={13} aria-hidden />
              </button>
            )}
          </span>
        );
      })}
    </>
  );
}

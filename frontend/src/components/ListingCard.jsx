import { motion } from "framer-motion";
import { ImageOff, ArrowRight, Pencil, Trash2, Store } from "lucide-react";
import { cn, mediaUrl } from "@/lib/utils";
import { PriceBadge, StatusBadge, TagPill } from "@/components/ui/badges";

// ListingCard — one saved listing in a grid. Click opens it in the workflow.
// QuickFlip listings show Edit / Delete; live eBay listings synced into the
// inventory manager (editable_ebay) show Edit price/qty + End instead.
export function ListingCard({ item, onOpen, onDelete, onEditLive, onEndLive, className }) {
  const l = item.listing || {};
  const thumb = l.images && l.images[0] ? mediaUrl(item.id, l.images[0]) : l.image_url || null;
  const inventory = item.status === "unlisted";
  const fromEbay = !!item.from_ebay;
  const liveEbay = fromEbay && item.editable_ebay;

  const open = () => {
    if (fromEbay && item.view_url) { window.open(item.view_url, "_blank", "noopener"); return; }
    onOpen(item.id);
  };

  return (
    <motion.div
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => { if (e.key === "Enter") open(); }}
      whileHover={{ y: -2, boxShadow: "var(--shadow-card-hover)" }}
      whileTap={{ scale: 0.985 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      className={cn(
        "text-left bg-card rounded-card border border-line shadow-card overflow-hidden",
        "flex flex-col cursor-pointer group relative",
        className,
      )}
    >
      <div className="aspect-[4/3] bg-bg-sunken relative overflow-hidden">
        {thumb ? (
          <img
            src={thumb}
            alt=""
            loading="lazy"
            className="size-full object-cover transition-transform duration-200 group-hover:scale-[1.03]"
            onError={(e) => { e.currentTarget.style.display = "none"; }}
          />
        ) : (
          <div className="grid place-items-center size-full text-ink-faint">
            <ImageOff size={28} aria-hidden />
          </div>
        )}
        <div className="absolute top-3 left-3 flex items-center gap-1.5">
          <StatusBadge status={item.status} className="shadow-card" />
          {fromEbay && (
            <TagPill tone="blue" className="shadow-card">
              <Store size={11} aria-hidden /> eBay
            </TagPill>
          )}
        </div>

        {/* Quick actions — ALWAYS visible so a delete is never hidden behind a
           hover (which doesn't exist on touch devices). */}
        <div className="absolute top-2.5 right-2.5 flex gap-1.5">
          {(!fromEbay || liveEbay) && (
            <button
              type="button"
              aria-label={liveEbay ? "Edit price and quantity" : "Edit listing"}
              title={liveEbay ? "Edit price / quantity" : "Edit"}
              onClick={(e) => {
                e.stopPropagation();
                if (liveEbay) onEditLive?.(item); else onOpen(item.id);
              }}
              className="grid place-items-center size-9 rounded-full bg-card text-ink shadow-float cursor-pointer hover:-translate-y-0.5 hover:bg-bg-sunken transition-all duration-150"
            >
              <Pencil size={16} aria-hidden />
            </button>
          )}
          {liveEbay && onEndLive && (
            <button
              type="button"
              aria-label="End listing on eBay"
              title="End listing"
              onClick={(e) => { e.stopPropagation(); onEndLive(item); }}
              className="grid place-items-center size-9 rounded-full bg-card text-error shadow-float cursor-pointer hover:-translate-y-0.5 hover:bg-red-soft transition-all duration-150"
            >
              <Trash2 size={16} aria-hidden />
            </button>
          )}
          {onDelete && !fromEbay && (
            <button
              type="button"
              aria-label="Delete listing"
              title="Delete"
              onClick={(e) => { e.stopPropagation(); onDelete(item); }}
              className="grid place-items-center size-9 rounded-full bg-card text-error shadow-float cursor-pointer hover:-translate-y-0.5 hover:bg-red-soft transition-all duration-150"
            >
              <Trash2 size={16} aria-hidden />
            </button>
          )}
        </div>
      </div>
      <div className="p-4 flex flex-col gap-2 flex-1">
        <p className="font-semibold text-sm text-ink line-clamp-2">
          {l.title || item.title || "(untitled)"}
        </p>
        <div className="mt-auto flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <PriceBadge value={l.price} currency={l.currency} approx={inventory} />
            {liveEbay && l.quantity != null && (
              <span className="text-xs font-medium text-ink-secondary whitespace-nowrap">
                Qty {l.quantity}
              </span>
            )}
          </div>
          {inventory && (
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-blue">
              Finish &amp; list <ArrowRight size={13} aria-hidden />
            </span>
          )}
          {fromEbay && (
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-blue shrink-0">
              View on eBay <ArrowRight size={13} aria-hidden />
            </span>
          )}
        </div>
      </div>
    </motion.div>
  );
}

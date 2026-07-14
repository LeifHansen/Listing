import { motion } from "framer-motion";
import { ImageOff, ArrowRight, Pencil, Trash2, Store } from "lucide-react";
import { cn, mediaUrl } from "@/lib/utils";
import { PriceBadge, StatusBadge, TagPill } from "@/components/ui/badges";

// ListingCard — one saved listing in a grid. Click opens it in the workflow;
// hover reveals Edit / Delete. Listings pulled from eBay wear an "eBay" badge
// and (being read-only mirrors) skip the delete action.
export function ListingCard({ item, onOpen, onDelete, className }) {
  const l = item.listing || {};
  const thumb = l.images && l.images[0] ? mediaUrl(item.id, l.images[0]) : l.image_url || null;
  const inventory = item.status === "unlisted";
  const fromEbay = !!item.from_ebay;

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

        {/* Hover actions */}
        <div className="absolute top-2.5 right-2.5 flex gap-1.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity duration-150">
          {!fromEbay && (
            <button
              type="button"
              aria-label="Edit listing"
              onClick={(e) => { e.stopPropagation(); onOpen(item.id); }}
              className="grid place-items-center size-8 rounded-full bg-card/95 text-ink shadow-float cursor-pointer hover:-translate-y-0.5 transition-transform duration-150"
            >
              <Pencil size={14} aria-hidden />
            </button>
          )}
          {onDelete && !fromEbay && (
            <button
              type="button"
              aria-label="Delete listing"
              onClick={(e) => { e.stopPropagation(); onDelete(item); }}
              className="grid place-items-center size-8 rounded-full bg-card/95 text-error shadow-float cursor-pointer hover:-translate-y-0.5 transition-transform duration-150"
            >
              <Trash2 size={14} aria-hidden />
            </button>
          )}
        </div>
      </div>
      <div className="p-4 flex flex-col gap-2 flex-1">
        <p className="font-semibold text-sm text-ink line-clamp-2">
          {l.title || item.title || "(untitled)"}
        </p>
        <div className="mt-auto flex items-center justify-between gap-2">
          <PriceBadge value={l.price} currency={l.currency} approx={inventory} />
          {inventory && (
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-blue">
              Finish &amp; list <ArrowRight size={13} aria-hidden />
            </span>
          )}
          {fromEbay && (
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-blue">
              View on eBay <ArrowRight size={13} aria-hidden />
            </span>
          )}
        </div>
      </div>
    </motion.div>
  );
}

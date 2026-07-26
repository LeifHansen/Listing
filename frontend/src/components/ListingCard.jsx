import { useState } from "react";
import { motion } from "framer-motion";
import { ImageOff, ArrowRight, Trash2, Eye, Heart } from "lucide-react";
import { cn, mediaUrl } from "@/lib/utils";
import { PriceBadge, StatusBadge } from "@/components/ui/badges";

// ListingCard — one saved listing in a grid. Click opens it in the workflow;
// when onDelete is provided, a trash button removes it. The delete control is a
// sibling of the card button (not nested) so it stays valid, focusable HTML.
// `metrics` (optional) shows eBay views/watchers for a live listing.
export function ListingCard({ item, onOpen, onDelete, metrics, className }) {
  const l = item.listing || {};
  const isLive = item.status === "published" || item.status === "live";
  const hasMetrics = isLive && metrics
    && (metrics.views != null || metrics.watchers != null);
  // Version thumbnails by updated_at so a rotate/clean-up busts the hour-long
  // /media cache the moment the listing is touched — without killing caching.
  const ver = Date.parse(item.updated_at || "") || undefined;
  // Listings imported from eBay have no local files — their photos are
  // eBay-hosted absolute URLs, used as-is.
  const thumb = l.images && l.images[0]
    ? mediaUrl(item.id, l.images[0], ver)
    : (l.image_urls && l.image_urls[0]) || null;
  const inventory = item.status === "unlisted";
  const fromEbay = (l.source || "") === "ebay";
  // eBay's own watch count rides along on imported listings, so those cards
  // aren't blank while the metrics endpoint only covers app-created ones.
  const watchers = hasMetrics ? metrics.watchers : (fromEbay ? l.watch_count : null);
  // An image that 404s (e.g. an older photo lost from ephemeral storage) shows
  // the placeholder instead of a blank tile.
  const [imgFailed, setImgFailed] = useState(false);

  return (
    <div className={cn("relative group", className)}>
      <motion.button
        type="button"
        onClick={() => onOpen(item.id)}
        whileHover={{ y: -2, boxShadow: "var(--shadow-card-hover)" }}
        whileTap={{ scale: 0.985 }}
        transition={{ duration: 0.18, ease: "easeOut" }}
        className={cn(
          "w-full text-left bg-card rounded-card border border-line shadow-card overflow-hidden",
          "flex flex-col cursor-pointer",
        )}
      >
        <div className="aspect-[4/3] bg-bg-sunken relative overflow-hidden">
          {thumb && !imgFailed ? (
            <img
              src={thumb}
              alt=""
              loading="lazy"
              className="size-full object-cover transition-transform duration-200 group-hover:scale-[1.03]"
              onError={() => setImgFailed(true)}
            />
          ) : (
            <div className="grid place-items-center size-full text-ink-faint">
              <ImageOff size={28} aria-hidden />
            </div>
          )}
          <StatusBadge status={item.status} className="absolute top-3 left-3 shadow-card" />
          {fromEbay && (
            <span
              className="absolute top-3 right-3 rounded-full bg-card/95 border border-line shadow-card px-2 py-0.5 text-[11px] font-bold text-ink-secondary"
              title="Imported from your eBay account — edits here update it on eBay"
            >
              On eBay
            </span>
          )}
        </div>
        <div className="p-4 flex flex-col gap-2 flex-1">
          <p className="font-semibold text-sm text-ink line-clamp-2">
            {l.title || item.title || "(untitled)"}
          </p>
          {(hasMetrics || watchers != null) && (
            <div className="flex items-center gap-3.5 text-[12px] font-medium text-ink-secondary">
              {hasMetrics && metrics.views != null && (
                <span className="inline-flex items-center gap-1" title="Views (last 30 days)">
                  <Eye size={13} aria-hidden /> {metrics.views}
                </span>
              )}
              {watchers != null && (
                <span className="inline-flex items-center gap-1" title="Watchers">
                  <Heart size={13} aria-hidden /> {watchers}
                </span>
              )}
            </div>
          )}
          <div className="mt-auto flex items-center justify-between gap-2">
            <PriceBadge value={l.price} currency={l.currency} approx={inventory} />
            {inventory && (
              <span className="inline-flex items-center gap-1 text-xs font-semibold text-blue">
                Finish &amp; list <ArrowRight size={13} aria-hidden />
              </span>
            )}
            {(item.status === "published" || item.status === "live") && (
              <span className="inline-flex items-center gap-1 text-xs font-semibold text-blue">
                Edit live <ArrowRight size={13} aria-hidden />
              </span>
            )}
            {item.status === "ended" && (
              <span className="inline-flex items-center gap-1 text-xs font-semibold text-blue">
                Relist <ArrowRight size={13} aria-hidden />
              </span>
            )}
          </div>
        </div>
      </motion.button>

      {onDelete && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onDelete(item); }}
          aria-label="Delete listing"
          title="Delete listing"
          className={cn(
            "absolute top-3 right-3 z-10 grid place-items-center size-8 rounded-full cursor-pointer",
            "bg-card/85 backdrop-blur border border-line shadow-card text-ink-faint",
            "hover:text-error hover:border-error/40 transition-colors",
          )}
        >
          <Trash2 size={15} aria-hidden />
        </button>
      )}
    </div>
  );
}

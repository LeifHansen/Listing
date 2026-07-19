import { useState } from "react";
import { motion } from "framer-motion";
import { ImageOff, ArrowRight, Trash2 } from "lucide-react";
import { cn, mediaUrl } from "@/lib/utils";
import { PriceBadge, StatusBadge } from "@/components/ui/badges";

// ListingCard — one saved listing in a grid. Click opens it in the workflow;
// when onDelete is provided, a trash button removes it. The delete control is a
// sibling of the card button (not nested) so it stays valid, focusable HTML.
export function ListingCard({ item, onOpen, onDelete, className }) {
  const l = item.listing || {};
  // Version thumbnails by updated_at so a rotate/clean-up busts the hour-long
  // /media cache the moment the listing is touched — without killing caching.
  const ver = Date.parse(item.updated_at || "") || undefined;
  const thumb = l.images && l.images[0] ? mediaUrl(item.id, l.images[0], ver) : null;
  const inventory = item.status === "unlisted";
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

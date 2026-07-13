import { motion } from "framer-motion";
import { ImageOff, ArrowRight } from "lucide-react";
import { cn, mediaUrl } from "@/lib/utils";
import { PriceBadge, StatusBadge } from "@/components/ui/badges";

// ListingCard — one saved listing in a grid. Click opens it in the workflow.
export function ListingCard({ item, onOpen, className }) {
  const l = item.listing || {};
  const thumb = l.images && l.images[0] ? mediaUrl(item.id, l.images[0]) : null;
  const inventory = item.status === "unlisted";

  return (
    <motion.button
      type="button"
      onClick={() => onOpen(item.id)}
      whileHover={{ y: -2, boxShadow: "var(--shadow-card-hover)" }}
      whileTap={{ scale: 0.985 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      className={cn(
        "text-left bg-card rounded-card border border-line shadow-card overflow-hidden",
        "flex flex-col cursor-pointer group",
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
  );
}

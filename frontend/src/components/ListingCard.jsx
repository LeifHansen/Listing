import { useState } from "react";
import { motion } from "framer-motion";
import {
  ImageOff, ArrowRight, Trash2, Eye, Heart, Check, RotateCcw, Loader2,
  SkipForward, Undo2, Clock, AlertTriangle, Ban,
} from "lucide-react";
import { cn, mediaUrl } from "@/lib/utils";
import { OriginBadge, PriceBadge, StatusBadge } from "@/components/ui/badges";

// ListingCard — one saved listing in a grid. Click opens it in the workflow;
// when onDelete is provided, a trash button removes it. The delete control is a
// sibling of the card button (not nested) so it stays valid, focusable HTML.
// `metrics` (optional) shows eBay views/watchers for a live listing.
// In select mode (`selectable`), clicking toggles `selected` via `onSelect`
// instead of opening — powers mass actions like delete-selected in Drafts.
export function ListingCard({
  item, onOpen, onDelete, onEnd, ending, onStartOver, startingOver, onSkip, skipped,
  stale, metrics, selectable, selected, onSelect, className,
}) {
  const l = item.listing || {};
  // Drafts with AI-inferred specifics awaiting a glance get a ⚠ count chip —
  // review those fields and the draft is publish-ready.
  const reviewCount = (item.status === "draft" || item.status === "dry_run")
    ? (l.item_specifics || []).filter(
      (s) => (s.value || "").trim() && s.confidence === "medium").length
    : 0;
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
        onClick={() => (selectable ? onSelect?.() : onOpen(item.id))}
        aria-pressed={selectable ? !!selected : undefined}
        whileHover={{ y: -2, boxShadow: "var(--shadow-card-hover)" }}
        whileTap={{ scale: 0.985 }}
        transition={{ duration: 0.18, ease: "easeOut" }}
        className={cn(
          "w-full text-left bg-card rounded-card border shadow-card overflow-hidden",
          "flex flex-col cursor-pointer",
          selectable && selected ? "border-blue ring-2 ring-blue/60" : "border-line",
          // A skipped draft stays fully usable — just visibly set aside.
          skipped && !selectable && "opacity-60",
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
          {/* Stale: live for 60+ days — relisting fresh gets a new item id and
              a search-placement boost. */}
          {stale && (
            <span
              className="absolute bottom-3 left-3 inline-flex items-center gap-1 rounded-full bg-yellow-soft border border-warning/30 shadow-card px-2 py-0.5 text-[11px] font-bold text-warning"
              title="Live for 60+ days — old listings sink in eBay search. Open it and relist fresh for a placement boost."
            >
              <Clock size={11} aria-hidden /> stale
            </span>
          )}
          {reviewCount > 0 && (
            <span
              className="absolute bottom-3 left-3 inline-flex items-center gap-1 rounded-full bg-yellow-soft border border-warning/30 shadow-card px-2 py-0.5 text-[11px] font-bold text-warning"
              title="AI-inferred item specifics worth a glance before publishing"
            >
              <AlertTriangle size={11} aria-hidden /> {reviewCount} to review
            </span>
          )}
          {/* Origin chip — created here vs. imported from eBay — only where
              the distinction matters (the listing exists on eBay). Hover for
              exactly what each kind allows. */}
          {(isLive || item.status === "ended" || item.status === "sold") && (
            <OriginBadge item={item}
              className="absolute bottom-3 right-3 [&>span]:shadow-card [&>span]:bg-card/95 [&>span]:border [&>span]:border-line" />
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

      {selectable ? (
        <span
          aria-hidden
          className={cn(
            "absolute top-3 right-3 z-10 grid place-items-center size-7 rounded-full",
            "border-2 shadow-card pointer-events-none transition-colors",
            selected ? "bg-blue border-blue text-on-accent" : "bg-card/90 border-line-strong text-transparent",
          )}
        >
          <Check size={15} strokeWidth={3} />
        </span>
      ) : (onDelete || onEnd || onStartOver || onSkip) && (
        <div className="absolute top-3 right-3 z-10 flex items-center gap-1.5">
          {/* Skip: set this draft aside. It stays in Drafts, but the queue
              after a publish stops offering it as the next one to work on. */}
          {onSkip && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onSkip(item); }}
              aria-pressed={!!skipped}
              aria-label={skipped ? "Un-skip this draft" : "Skip this draft"}
              title={skipped
                ? "Skipped — won't be offered as the next draft. Click to undo."
                : "Skip — set aside so it isn't offered as the next draft"}
              className={cn(
                "grid place-items-center size-8 rounded-full cursor-pointer",
                "backdrop-blur border shadow-card transition-colors",
                skipped
                  ? "bg-blue border-blue text-on-accent"
                  : "bg-card/85 border-line text-ink-faint hover:text-blue hover:border-blue/40",
              )}
            >
              {skipped ? <Undo2 size={15} aria-hidden /> : <SkipForward size={15} aria-hidden />}
            </button>
          )}
          {/* Start over: re-run the AI on this listing's own photos, replacing
              the drafted content. Drafts only — it would overwrite a live
              listing's copy with a fresh guess. */}
          {onStartOver && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onStartOver(item); }}
              disabled={startingOver}
              aria-label="Start over — re-run the AI on these photos"
              title="Start over — re-run the AI on these photos"
              className={cn(
                "grid place-items-center size-8 rounded-full cursor-pointer",
                "bg-card/85 backdrop-blur border border-line shadow-card text-ink-faint",
                "hover:text-blue hover:border-blue/40 transition-colors",
                startingOver && "cursor-wait text-blue",
              )}
            >
              {startingOver
                ? <Loader2 size={15} className="animate-spin" aria-hidden />
                : <RotateCcw size={15} aria-hidden />}
            </button>
          )}
          {/* A LIVE listing's card action is End (→ Inactive tab), never a
              permanent delete: the listing is a real thing on eBay. Delete
              stays for drafts/finds and already-inactive records. */}
          {onEnd ? (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onEnd(item); }}
              disabled={ending}
              aria-label="End listing on eBay"
              title="End this listing on eBay — it moves to Inactive and can be relisted anytime"
              className={cn(
                "grid place-items-center size-8 rounded-full cursor-pointer",
                "bg-card/85 backdrop-blur border border-line shadow-card text-ink-faint",
                "hover:text-error hover:border-error/40 transition-colors",
                ending && "cursor-wait text-error",
              )}
            >
              {ending
                ? <Loader2 size={15} className="animate-spin" aria-hidden />
                : <Ban size={15} aria-hidden />}
            </button>
          ) : onDelete && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onDelete(item); }}
              aria-label="Delete listing"
              title="Delete listing"
              className={cn(
                "grid place-items-center size-8 rounded-full cursor-pointer",
                "bg-card/85 backdrop-blur border border-line shadow-card text-ink-faint",
                "hover:text-error hover:border-error/40 transition-colors",
              )}
            >
              <Trash2 size={15} aria-hidden />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

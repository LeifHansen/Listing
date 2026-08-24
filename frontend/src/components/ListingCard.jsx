import { useState } from "react";
import { motion } from "framer-motion";
import {
  ImageOff, ArrowRight, Trash2, Eye, Heart, Check, RotateCcw, Loader2,
  SkipForward, Undo2, Clock, AlertTriangle, Ban,
} from "lucide-react";
import { cn, formatMoney, mediaUrl } from "@/lib/utils";
import { OriginBadge, PriceBadge, StatusBadge } from "@/components/ui/badges";
import { hasSalePrice, saleDiscount, salePrice } from "@/lib/sales";
import { reviewAspectCount } from "@/views/listing/specifics";

// Views / watchers on a live listing — eBay's traffic, where we have it.
function MetricsRow({ views, watchers, className }) {
  if (views == null && watchers == null) return null;
  return (
    <div className={cn(
      "flex items-center gap-3.5 text-[12px] font-medium text-ink-secondary", className)}>
      {views != null && (
        <span className="inline-flex items-center gap-1" title="Views (last 30 days)">
          <Eye size={13} aria-hidden /> {views}
        </span>
      )}
      {watchers != null && (
        <span className="inline-flex items-center gap-1" title="Watchers">
          <Heart size={13} aria-hidden /> {watchers}
        </span>
      )}
    </div>
  );
}

// Cross-posting chips: where else this listing lives (Etsy, Depop, ...).
// eBay stays implied by the origin/status badges.
function MarketplaceChips({ listing }) {
  const others = Object.entries(listing.marketplaces || {})
    .filter(([key, st]) => key !== "ebay" && st && (st.status || st.error));
  if (!others.length) return null;
  const label = (key) => key.charAt(0).toUpperCase() + key.slice(1);
  return (
    <>
      {others.map(([key, st]) => (
        <span
          key={key}
          title={st.error ? `${label(key)}: ${st.error}` : `${label(key)}: ${st.status}`}
          className={cn(
            "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-bold",
            st.error
              ? "bg-warning-soft border-warning/40 text-warning"
              : st.status === "published"
                ? "bg-success-soft border-success/30 text-success"
                : "bg-bg-sunken border-line text-ink-secondary",
          )}
        >
          {label(key)} {st.error ? "!" : st.status === "published" ? "✓" : st.status}
        </span>
      ))}
    </>
  );
}

// Live for 60+ days: old listings sink in eBay search, so relisting fresh is
// worth surfacing on the card itself.
function StaleChip({ className }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full bg-yellow-soft border border-warning/30",
        "px-2 py-0.5 text-[11px] font-bold text-warning", className)}
      title="Live for 60+ days — old listings sink in eBay search. Open it and relist fresh for a placement boost."
    >
      <Clock size={11} aria-hidden /> stale
    </span>
  );
}

function ReviewChip({ count, className }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full bg-yellow-soft border border-warning/30",
        "px-2 py-0.5 text-[11px] font-bold text-warning", className)}
      title="AI-inferred item specifics worth a glance before publishing"
    >
      <AlertTriangle size={11} aria-hidden /> {count} to review
    </span>
  );
}

// What it actually went for. An accepted offer settles BELOW the asking price
// and eBay never moves the listing's own price, so a sold card showing `price`
// was showing what was asked. The ask stays visible, struck through, with how
// far under it landed — plus the profit where a cost basis was recorded.
function SoldLines({ listing: l, soldFor, knownSale, discount, className }) {
  const paid = l.purchase_price;
  if (!discount && !(paid != null && soldFor > 0)) return null;
  return (
    <div className={cn("flex flex-wrap items-center gap-x-2 gap-y-1", className)}>
      {discount && (
        <p className="flex items-center gap-1.5 text-[12px] font-semibold text-ink-secondary"
          title={`Sold for ${formatMoney(soldFor, l.currency)} — ${formatMoney(discount.amount, l.currency)} (${discount.percent}%) under the ${formatMoney(l.price, l.currency)} asking price.`}>
          <span className="line-through text-ink-faint tabular-nums">
            {formatMoney(l.price, l.currency)}
          </span>
          <span className="rounded-full bg-blue-soft px-1.5 py-0.5 text-[11px] font-bold text-blue tabular-nums">
            −{discount.percent}%
          </span>
        </p>
      )}
      {/* Profit framework: a sold item with a recorded cost basis shows what
          it made (sale price − what you paid, before fees). */}
      {paid != null && soldFor > 0 && (
        <p className="text-[12px] font-semibold"
          title={`Sold ${knownSale ? "" : "~"}${formatMoney(soldFor, l.currency)} − paid ${formatMoney(paid, l.currency)}, before fees & shipping`}>
          <span className={soldFor - Number(paid) >= 0 ? "text-success" : "text-warning"}>
            {soldFor - Number(paid) >= 0 ? "+" : "−"}$
            {Math.abs(soldFor - Number(paid)).toFixed(2)} profit
          </span>
        </p>
      )}
    </div>
  );
}

// The one-line "what happens if you click this" for the card's status.
const CTA = { unlisted: "Finish & list", published: "Edit live", live: "Edit live", ended: "Relist" };

function CtaHint({ status, className }) {
  const text = CTA[status];
  if (!text) return null;
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs font-semibold text-blue", className)}>
      {text} <ArrowRight size={13} aria-hidden />
    </span>
  );
}

// ListingCard — one saved listing, as a grid tile (`layout="grid"`, the
// default) or as a compact row (`layout="list"`). Click opens it in the
// workflow; when onDelete is provided, a trash button removes it. The delete
// control is a sibling of the card button (not nested) so it stays valid,
// focusable HTML — in list layout it sits beside the row instead of over it.
// `metrics` (optional) shows eBay views/watchers for a live listing.
// In select mode (`selectable`), clicking toggles `selected` via `onSelect`
// instead of opening — powers mass actions like delete-selected in Drafts.
export function ListingCard({
  item, onOpen, onDelete, onEnd, ending, onStartOver, startingOver, onSkip, skipped,
  stale, metrics, selectable, selected, onSelect, layout = "grid", className,
}) {
  const list = layout === "list";
  const l = item.listing || {};
  // Drafts with AI-inferred specifics awaiting a glance get a ⚠ count chip —
  // review those fields and the draft is publish-ready.
  const reviewCount = (item.status === "draft" || item.status === "dry_run")
    ? reviewAspectCount(l.item_specifics)
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
  // A sold listing is shown at what the buyer PAID (l.sold_price) whenever
  // eBay reported it; without that we fall back to the asking price and mark
  // the badge approximate rather than quietly claiming it as the take.
  const sold = item.status === "sold";
  const soldFor = sold ? salePrice(l) : null;
  const knownSale = hasSalePrice(l);
  const discount = sold ? saleDiscount(l) : null;
  const fromEbay = (l.source || "") === "ebay";
  // eBay's own watch count rides along on imported listings, so those cards
  // aren't blank while the metrics endpoint only covers app-created ones.
  const watchers = hasMetrics ? metrics.watchers : (fromEbay ? l.watch_count : null);
  // An image that 404s (e.g. an older photo lost from ephemeral storage) shows
  // the placeholder instead of a blank tile.
  const [imgFailed, setImgFailed] = useState(false);
  // Origin — created here vs. imported from eBay — only where the distinction
  // matters (the listing exists on eBay). Hover for exactly what each allows.
  const showOrigin = isLive || item.status === "ended" || item.status === "sold";

  const photo = thumb && !imgFailed ? (
    <img
      src={thumb}
      alt=""
      loading="lazy"
      className={cn("size-full object-cover transition-transform duration-200",
        !list && "group-hover:scale-[1.03]")}
      onError={() => setImgFailed(true)}
    />
  ) : (
    <div className="grid place-items-center size-full text-ink-faint">
      <ImageOff size={list ? 20 : 28} aria-hidden />
    </div>
  );

  const price = (
    <PriceBadge
      value={sold ? soldFor : l.price}
      currency={l.currency}
      approx={inventory || (sold && !knownSale)}
      title={sold
        ? (knownSale
          ? "What this actually sold for"
          : "eBay hasn't reported what this sold for — showing the asking price")
        : undefined}
    />
  );

  const actions = (onDelete || onEnd || onStartOver || onSkip) && (
    <>
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
    </>
  );

  const cardClass = cn(
    // h-full fills whatever height the caller gave the card, so a grid that
    // passes `className="h-full"` gets a row of cards that line up even when
    // one carries an extra line (a sold card's discount and profit rows).
    // Callers that put their own controls UNDER the card (the drafts strip)
    // leave it alone and keep a card sized to its own content.
    "w-full h-full text-left bg-card rounded-card border shadow-card overflow-hidden",
    "flex cursor-pointer",
    list ? "flex-row items-center gap-3 p-2.5 sm:p-3" : "flex-col",
    selectable && selected ? "border-blue ring-2 ring-blue/60" : "border-line",
    // A skipped draft stays fully usable — just visibly set aside.
    skipped && !selectable && "opacity-60",
  );

  const motionProps = {
    type: "button",
    onClick: () => (selectable ? onSelect?.() : onOpen(item.id)),
    "aria-pressed": selectable ? !!selected : undefined,
    whileHover: { y: -2, boxShadow: "var(--shadow-card-hover)" },
    whileTap: { scale: 0.985 },
    transition: { duration: 0.18, ease: "easeOut" },
    className: cardClass,
  };

  // List layout: one scannable row per listing. Everything the tile stacks —
  // status, origin, warnings, traffic — moves onto a single wrapping badge
  // line, and the price/CTA pair sits at the end of the row where the eye
  // lands after the title.
  const body = list ? (
    <motion.button {...motionProps}>
      <span className="relative size-16 sm:size-20 shrink-0 overflow-hidden rounded-tile bg-bg-sunken">
        {photo}
      </span>
      <span className="min-w-0 flex-1 flex flex-col gap-1.5">
        <span className="font-semibold text-sm text-ink line-clamp-2">
          {l.title || item.title || "(untitled)"}
        </span>
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <StatusBadge status={item.status} />
          {showOrigin && <OriginBadge item={item} />}
          {stale && <StaleChip />}
          {reviewCount > 0 && <ReviewChip count={reviewCount} />}
          <MarketplaceChips listing={l} />
          {(hasMetrics || watchers != null) && (
            <MetricsRow views={hasMetrics ? metrics.views : null} watchers={watchers} />
          )}
        </span>
        {sold && (
          <SoldLines listing={l} soldFor={soldFor} knownSale={knownSale} discount={discount} />
        )}
        {/* Phone: the price/CTA column would squeeze the badge line into one
            chip per row, so it folds into the content instead. */}
        <span className="flex sm:hidden items-center gap-2.5">
          {price}
          <CtaHint status={item.status} className="whitespace-nowrap" />
        </span>
      </span>
      <span className="hidden sm:flex shrink-0 flex-col items-end gap-1.5 pl-1">
        {price}
        <CtaHint status={item.status} className="whitespace-nowrap" />
      </span>
    </motion.button>
  ) : (
    <motion.button {...motionProps}>
      <div className="aspect-[4/3] bg-bg-sunken relative overflow-hidden">
        {photo}
        <StatusBadge status={item.status} className="absolute top-3 left-3 shadow-card" />
        {stale && <StaleChip className="absolute bottom-3 left-3 shadow-card" />}
        {reviewCount > 0 && (
          <ReviewChip count={reviewCount} className="absolute bottom-3 left-3 shadow-card" />
        )}
        {showOrigin && (
          <OriginBadge item={item}
            className="absolute bottom-3 right-3 [&>span]:shadow-card [&>span]:bg-card/95 [&>span]:border [&>span]:border-line" />
        )}
      </div>
      <div className="p-4 flex flex-col gap-2 flex-1">
        <p className="font-semibold text-sm text-ink line-clamp-2">
          {l.title || item.title || "(untitled)"}
        </p>
        {(hasMetrics || watchers != null) && (
          <MetricsRow views={hasMetrics ? metrics.views : null} watchers={watchers} />
        )}
        <div className="flex flex-wrap items-center gap-1.5 empty:hidden">
          <MarketplaceChips listing={l} />
        </div>
        {sold && (
          <SoldLines listing={l} soldFor={soldFor} knownSale={knownSale} discount={discount}
            className="flex-col items-start" />
        )}
        <div className="mt-auto flex items-center justify-between gap-2">
          {price}
          <CtaHint status={item.status} />
        </div>
      </div>
    </motion.button>
  );

  return (
    <div className={cn("relative group", list && "flex items-center gap-2", className)}>
      {body}
      {selectable ? (
        <span
          aria-hidden
          className={cn(
            "grid place-items-center size-7 rounded-full",
            "border-2 shadow-card pointer-events-none transition-colors",
            list ? "shrink-0 order-first" : "absolute top-3 right-3 z-10",
            selected ? "bg-blue border-blue text-on-accent" : "bg-card/90 border-line-strong text-transparent",
          )}
        >
          <Check size={15} strokeWidth={3} />
        </span>
      ) : actions && (
        <div className={cn(
          "z-10 flex items-center gap-1.5",
          list ? "shrink-0" : "absolute top-3 right-3",
        )}>
          {actions}
        </div>
      )}
    </div>
  );
}

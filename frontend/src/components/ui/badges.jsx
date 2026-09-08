import { CheckCircle2, AlertCircle, Circle, Gavel } from "lucide-react";
import { cn, formatMoney } from "@/lib/utils";
import {
  formatChipLabel, formatSummary, isAuctionFormat, listingFormat,
} from "@/lib/listingFormat";

export function TagPill({ children, tone = "neutral", className, title }) {
  const tones = {
    neutral: "bg-bg-sunken text-ink-secondary",
    blue: "bg-blue-soft text-blue",
    green: "bg-green-soft text-green",
    yellow: "bg-yellow-soft text-warning",
    red: "bg-red-soft text-error",
  };
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 font-display text-xs font-semibold",
        tones[tone] || tones.neutral,
        className,
      )}
    >
      {children}
    </span>
  );
}

// `prefix` names the number when it is not a plain asking price -- "Bid" on
// an auction, whose money field is the opening bid and not what the item
// costs. Without it an auction starting at $0.99 sits in a grid of Buy It Now
// prices looking like a $0.99 item (see lib/listingFormat.askingPrice).
export function PriceBadge({
  value, currency = "USD", approx = false, prefix, className, title,
}) {
  const text = formatMoney(value, currency);
  if (!text) return <TagPill className={className} title={title}>no price yet</TagPill>;
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-baseline gap-0.5 rounded-full bg-green-soft px-2.5 py-0.5",
        "font-display text-[13px] font-bold text-green tabular-nums",
        className,
      )}
    >
      {prefix && (
        <span className="mr-0.5 text-[11px] font-semibold uppercase tracking-wide opacity-75">
          {prefix}
        </span>
      )}
      {approx && <span className="font-semibold">≈</span>}
      {text}
    </span>
  );
}

// How this listing sells, when that is not the obvious answer.
//
// Buy It Now draws NOTHING: it is the default and by far the common case, and
// a chip on every card in the store would be noise that teaches the eye to
// skip the row the auctions need it to read. The two auction formats say so,
// and the tooltip carries the numbers the chip has no room for -- opening
// bid, Buy It Now, how long it runs.
export function FormatBadge({ listing, className }) {
  const fmt = listingFormat(listing);
  if (!isAuctionFormat(fmt)) return null;
  const l = listing || {};
  return (
    <span
      title={formatSummary(l, (v) => formatMoney(v, l.currency || "USD"))}
      className={cn(
        "inline-flex items-center gap-1 rounded-full bg-blue-soft border border-blue/25",
        "px-2 py-0.5 text-[11px] font-bold text-blue", className,
      )}
    >
      <Gavel size={11} aria-hidden />
      {formatChipLabel(fmt)}
    </span>
  );
}

const STATUS_META = {
  published: { label: "Live on eBay", tone: "green" },
  live: { label: "Live on eBay", tone: "green" },
  draft: { label: "Draft", tone: "blue" },
  dry_run: { label: "Dry run", tone: "yellow" },
  unlisted: { label: "Unlisted find", tone: "yellow" },
  ended: { label: "Ended", tone: "neutral" },
  sold: { label: "Sold", tone: "green" },
};

export function StatusBadge({ status, className }) {
  const meta = STATUS_META[status] || { label: status || "—", tone: "neutral" };
  return <TagPill tone={meta.tone} className={className}>{meta.label}</TagPill>;
}

// ProgressChip — the state chip on each workflow card, in exactly three
// meanings. The amber one is the load-bearing distinction: it says eBay
// refuses this listing until the card is dealt with, so it may only appear on
// a card holding a real blocker (see blockers.js). "Needs attention" is what
// it used to say, and it sat on merely-unfinished cards too — which is how a
// seller ended up unable to tell a rejection from a suggestion.
export function ProgressChip({ state = "todo", className }) {
  if (state === "complete") {
    return (
      <TagPill tone="green" className={className}>
        <CheckCircle2 size={13} strokeWidth={2.5} aria-hidden /> Complete
      </TagPill>
    );
  }
  if (state === "attention") {
    return (
      <TagPill tone="yellow" className={className}
        title="eBay won't accept the listing until this is fixed">
        <AlertCircle size={13} strokeWidth={2.5} aria-hidden /> Blocks publish
      </TagPill>
    );
  }
  return (
    <TagPill className={className}>
      <Circle size={11} strokeWidth={2.5} aria-hidden /> Optional
    </TagPill>
  );
}

// ---------------------------------------------------------------------------
// Listing origin — created here vs. published on eBay and imported — and what
// that means for editing. The record id is the reliable tell: imported
// listings are stored as "ebay-<itemId>", app-created ones as session ids.
// (`listing.source === "ebay"` alone can't distinguish them: app listings
// published through the Trading API set that too.)
// ---------------------------------------------------------------------------

export function originOf(item) {
  if (String(item.id || "").startsWith("ebay-")) return "imported";
  return "app";
}

export const ORIGIN_META = {
  app: {
    label: "Thryft",
    tone: "blue",
    tip: "Created with Thryft Shop. Fully editable here and in eBay Seller Hub — no restrictions.",
  },
  imported: {
    label: "eBay import",
    tone: "neutral",
    tip: "Published on eBay, mirrored here. Fully editable from this app — edited photos upload as fresh copies to eBay. Two limits: the format (auction vs Buy It Now) can't be changed after publishing, and listings with variations aren't supported yet.",
  },
};

// The eBay wordmark, per-letter brand colors — instantly readable as "this
// lives on eBay" without spelling it out.
export function EbayMark({ className }) {
  return (
    <span aria-label="eBay"
      /* font-sans is explicit: this sits inside a TagPill, which is font-display,
         and the wordmark must not inherit Fredoka (no italic, no 800). */
      className={cn("font-sans font-extrabold italic tracking-tight leading-none select-none", className)}>
      <span style={{ color: "#E53238" }}>e</span>
      <span style={{ color: "#0064D2" }}>b</span>
      <span style={{ color: "#F5AF02" }}>a</span>
      <span style={{ color: "#86B817" }}>y</span>
    </span>
  );
}

// One origin chip, used on cards and in the legend. Hover/long-press shows
// the rules. Imported listings show the eBay mark instead of a text label.
export function OriginChip({ kind, className }) {
  const meta = ORIGIN_META[kind] || ORIGIN_META.app;
  return (
    <span title={meta.tip} className={cn("cursor-help", className)}>
      <TagPill tone={meta.tone}>
        {kind === "imported" ? <EbayMark className="text-[12px]" /> : meta.label}
      </TagPill>
    </span>
  );
}

export function OriginBadge({ item, className }) {
  return <OriginChip kind={originOf(item)} className={className} />;
}

export function ConfidenceBadge({ level, className }) {
  const l = ["low", "medium", "high"].includes(level) ? level : "medium";
  const tone = { low: "red", medium: "yellow", high: "green" }[l];
  return (
    <TagPill tone={tone} className={className}>
      AI confidence: {l}
    </TagPill>
  );
}

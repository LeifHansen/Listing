import { CheckCircle2, AlertCircle, Circle } from "lucide-react";
import { cn, formatMoney } from "@/lib/utils";

export function TagPill({ children, tone = "neutral", className }) {
  const tones = {
    neutral: "bg-bg-sunken text-ink-secondary",
    blue: "bg-blue-soft text-blue",
    green: "bg-green-soft text-green",
    yellow: "bg-yellow-soft text-warning",
    red: "bg-red-soft text-error",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold",
        tones[tone] || tones.neutral,
        className,
      )}
    >
      {children}
    </span>
  );
}

export function PriceBadge({ value, currency = "USD", approx = false, className }) {
  const text = formatMoney(value, currency);
  if (!text) return <TagPill className={className}>no price yet</TagPill>;
  return (
    <span
      className={cn(
        "inline-flex items-baseline gap-0.5 rounded-full bg-green-soft px-2.5 py-0.5",
        "text-[13px] font-bold text-green tabular-nums",
        className,
      )}
    >
      {approx && <span className="font-semibold">≈</span>}
      {text}
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

// ProgressChip — the "✔ Complete / Needs attention" chip on workflow cards.
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
      <TagPill tone="yellow" className={className}>
        <AlertCircle size={13} strokeWidth={2.5} aria-hidden /> Needs attention
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
  const l = item.listing || {};
  if (String(item.id || "").startsWith("ebay-")) return "imported";
  const live = item.status === "published" || item.status === "live";
  // Live but NOT Trading-routed and carrying an eBay id = published back when
  // the app used the Inventory API. eBay locks those out of Seller Hub's own
  // editors, so the seller needs to know edits belong here.
  if (live && (l.source || "") !== "ebay" && l.ebay_listing_id) return "app_classic";
  return "app";
}

export const ORIGIN_META = {
  app: {
    label: "Thryft",
    tone: "blue",
    tip: "Created with Thryft Shop. Fully editable here and in eBay Seller Hub — no restrictions.",
  },
  app_classic: {
    label: "Thryft · app-managed",
    tone: "yellow",
    tip: "Created with Thryft Shop before the Seller Hub upgrade. eBay doesn't let its own Seller Hub edit this kind of listing — make changes here and they go live. Ending and relisting it converts it to a fully unrestricted listing.",
  },
  imported: {
    label: "eBay import",
    tone: "neutral",
    tip: "Published on eBay, mirrored here. Fully editable from this app — edited photos upload as fresh copies to eBay. Two limits: the format (auction vs Buy It Now) can't be changed after publishing, and listings with variations aren't supported yet.",
  },
};

// The origin chip on a listing card. Hover/long-press shows the rules.
export function OriginBadge({ item, className }) {
  const meta = ORIGIN_META[originOf(item)];
  return (
    <span title={meta.tip} className={cn("cursor-help", className)}>
      <TagPill tone={meta.tone}>{meta.label}</TagPill>
    </span>
  );
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

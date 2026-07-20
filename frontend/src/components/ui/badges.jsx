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

export function ConfidenceBadge({ level, className }) {
  const l = ["low", "medium", "high"].includes(level) ? level : "medium";
  const tone = { low: "red", medium: "yellow", high: "green" }[l];
  return (
    <TagPill tone={tone} className={className}>
      AI confidence: {l}
    </TagPill>
  );
}

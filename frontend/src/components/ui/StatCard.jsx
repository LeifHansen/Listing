import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

// StatCard — one glanceable number with an icon chip. With `onClick` it
// becomes a real button (the dashboard tiles jump to the matching view).
// `action` is an optional control (the sold tile's time-range picker) rendered
// in the top-right corner as a SIBLING of that button, never inside it —
// nesting an interactive element in a button is invalid HTML and the inner
// control stops being clickable.
export function StatCard({ icon: Icon, label, value, sub, tone = "blue", className, onClick, action }) {
  const tones = {
    blue: "bg-blue-soft text-blue",
    green: "bg-green-soft text-green",
    yellow: "bg-yellow-soft text-warning",
    red: "bg-red-soft text-error",
  };
  const Comp = onClick ? motion.button : motion.div;
  const card = (
    <Comp
      type={onClick ? "button" : undefined}
      onClick={onClick}
      whileHover={{ y: -2, boxShadow: "var(--shadow-card-hover)" }}
      whileTap={onClick ? { scale: 0.98 } : undefined}
      transition={{ duration: 0.18, ease: "easeOut" }}
      className={cn(
        "bg-card rounded-card border border-line shadow-card p-5",
        onClick && "text-left w-full cursor-pointer",
        action ? "h-full" : className,
      )}
    >
      {/* Room for the corner control, so a long label never runs under it. */}
      <div className={cn("flex items-center gap-3", action && "pr-24")}>
        <span className={cn("grid place-items-center size-10 rounded-[13px] shrink-0", tones[tone])}>
          <Icon size={19} strokeWidth={2} aria-hidden />
        </span>
        <span className="font-display text-[13px] font-semibold text-ink-secondary">{label}</span>
      </div>
      <p className="font-display mt-3 text-[28px] leading-none font-bold text-ink tabular-nums">{value}</p>
      {sub && <p className="mt-1.5 text-xs text-ink-faint">{sub}</p>}
    </Comp>
  );
  if (!action) return card;
  return (
    <div className={cn("relative", className)}>
      {card}
      <div className="absolute top-3 right-3 z-10">{action}</div>
    </div>
  );
}

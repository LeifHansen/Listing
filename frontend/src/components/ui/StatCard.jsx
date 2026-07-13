import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

// StatCard — one glanceable number with an icon chip.
export function StatCard({ icon: Icon, label, value, sub, tone = "blue", className }) {
  const tones = {
    blue: "bg-blue-soft text-blue",
    green: "bg-green-soft text-green",
    yellow: "bg-yellow-soft text-warning",
    red: "bg-red-soft text-error",
  };
  return (
    <motion.div
      whileHover={{ y: -2, boxShadow: "var(--shadow-card-hover)" }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      className={cn("bg-card rounded-card border border-line shadow-card p-5", className)}
    >
      <div className="flex items-center gap-3">
        <span className={cn("grid place-items-center size-10 rounded-[13px] shrink-0", tones[tone])}>
          <Icon size={19} strokeWidth={2} aria-hidden />
        </span>
        <span className="text-[13px] font-semibold text-ink-secondary">{label}</span>
      </div>
      <p className="mt-3 text-[28px] leading-none font-bold text-ink tabular-nums">{value}</p>
      {sub && <p className="mt-1.5 text-xs text-ink-faint">{sub}</p>}
    </motion.div>
  );
}

import { forwardRef } from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { InfoTip } from "@/components/ui/fields";

// AppCard — the floating white surface everything sits on.
export const Card = forwardRef(function Card(
  { className, hover = false, children, ...props },
  ref,
) {
  const Comp = hover ? motion.div : "div";
  const motionProps = hover
    ? {
        whileHover: { y: -2, boxShadow: "var(--shadow-card-hover)" },
        transition: { duration: 0.18, ease: "easeOut" },
      }
    : {};
  return (
    <Comp
      ref={ref}
      className={cn(
        "bg-card rounded-card border border-line shadow-card p-6",
        className,
      )}
      {...motionProps}
      {...props}
    >
      {children}
    </Comp>
  );
});

export function SectionHeader({ icon: Icon, title, hint, action, className }) {
  return (
    <div className={cn("flex items-start justify-between gap-3 mb-4", className)}>
      <div className="flex items-center gap-2.5 min-w-0">
        {Icon && (
          <span className="grid place-items-center size-9 rounded-[12px] bg-blue-soft text-blue shrink-0">
            <Icon size={18} strokeWidth={2} aria-hidden />
          </span>
        )}
        {/* Section explainers hide behind a hover ⓘ — headers stay one line. */}
        <div className="min-w-0 flex items-center gap-1.5">
          <h2 className="text-[17px] font-bold leading-tight text-ink">{title}</h2>
          {hint && <InfoTip text={String(hint)} />}
        </div>
      </div>
      {action}
    </div>
  );
}

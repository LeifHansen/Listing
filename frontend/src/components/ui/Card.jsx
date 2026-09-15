import { forwardRef } from "react";
import { cn } from "@/lib/utils";
import { InfoTip } from "@/components/ui/fields";

// AppCard — the floating white surface everything sits on.
//
// It used to take a `hover` prop that swapped the div for a motion.div and
// lifted the card on hover. No call site ever passed it, so the branch was
// dead and framer-motion was imported here for nothing. The cards that DO
// lift (StatCard, ShopMode, Dashboard, ListingCard) each animate themselves
// with the same whileHover, which is why this was never missed.
export const Card = forwardRef(function Card(
  { className, children, ...props },
  ref,
) {
  return (
    <div
      ref={ref}
      className={cn(
        "bg-card rounded-card border border-line shadow-card p-6",
        className,
      )}
      {...props}
    >
      {children}
    </div>
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

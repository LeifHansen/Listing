import { Card } from "@/components/ui/Card";
import { cn } from "@/lib/utils";

/**
 * One group of settings: a short title and a one-line description on the
 * left, the card of controls on the right. On a phone the two stack.
 *
 * The id is what the jump nav and the deep links scroll to
 * (`settings-<id>`); `scroll-mt` keeps the sticky nav from covering the
 * title when they do.
 */
export function SettingsSection({ id, title, description, children, className }) {
  return (
    <section
      id={`settings-${id}`}
      aria-labelledby={`settings-${id}-title`}
      className={cn("scroll-mt-24 grid gap-3 md:grid-cols-[minmax(0,220px)_1fr] md:gap-8", className)}
    >
      <div className="min-w-0 md:pt-1">
        <h2 id={`settings-${id}-title`} className="text-[17px] font-bold leading-tight text-ink">
          {title}
        </h2>
        {description && (
          <p className="text-sm text-ink-secondary mt-1 text-balance">{description}</p>
        )}
      </div>
      <Card className="min-w-0">{children}</Card>
    </section>
  );
}

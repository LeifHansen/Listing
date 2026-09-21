import { cn } from "@/lib/utils";

/* PageHeader — the top of a main-nav screen.
 *
 * List and Manage are two halves of one job, and the fastest way for a split
 * to read as two unrelated screens is for each to invent its own title. They
 * share this instead: the same icon chip, the same type ramp, the same gap.
 * It is SectionHeader's vocabulary (a soft blue tile, the title beside it) one
 * size up, so a page header and the section headers under it are visibly the
 * same family rather than two ideas about what a heading looks like.
 *
 * `action` is the right-hand slot for controls that belong to the whole page.
 * It wraps underneath on a phone rather than squeezing the title, which is
 * the bug the listings manager's own header had to be taught (see its
 * flex-wrap comment).
 */
export function PageHeader({ icon: Icon, title, subtitle, action, className }) {
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-3", className)}>
      <div className="flex items-center gap-3 min-w-0">
        {Icon && (
          <span
            className="grid place-items-center size-11 rounded-[14px] bg-blue-soft text-blue shrink-0"
            aria-hidden
          >
            <Icon size={22} strokeWidth={2} />
          </span>
        )}
        <div className="min-w-0">
          <h1 className="text-xl sm:text-2xl font-bold text-ink leading-tight truncate">
            {title}
          </h1>
          {subtitle && (
            <p className="text-sm text-ink-secondary mt-0.5">{subtitle}</p>
          )}
        </div>
      </div>
      {action && (
        <div className="flex flex-wrap items-center justify-end gap-2 ml-auto">
          {action}
        </div>
      )}
    </div>
  );
}

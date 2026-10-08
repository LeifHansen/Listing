import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertCircle, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { InfoTip } from "@/components/ui/fields";

/* Section — one part of the listing editor, on the one surface the editor is.
 *
 * This replaces WorkflowCard. That drew every part of the editor as its own
 * card: an icon tile, a title, a "Complete / Optional / Blocks publish" chip,
 * a chevron, a shadow, and a gap to the next card -- ten of them in a column,
 * each a box to look into. It was eBay's form in nicer colours: one section
 * per thing eBay asks, each with its own frame (LISTING_REDESIGN.md, "one
 * page, five decisions"). The chrome is gone. A section is a heading, a rule
 * above it, and its fields; the editor is one page.
 *
 * What is kept is everything the rest of the editor depends on, because none
 * of it was about the chrome:
 *
 *  - `id` names the section the way eBay's refusals and the publish bar's
 *    blocker chips name a field group (title, price, specifics, ...), and the
 *    DOM id `card-<id>` keeps the old anchor.
 *  - `flagged` opens the section and scrolls it into view, on a false→true
 *    transition only (a seller who folds a flagged section keeps it folded;
 *    the publish bar re-triggers by clearing and re-setting the target on the
 *    next frame). `expand` opens it the same way but claims no scroll: two
 *    sections fighting over scrollIntoView leaves the seller wherever the
 *    race landed. Both are the WorkflowCard contract, verbatim.
 *  - `state` still says "attention" when something in here is keeping the
 *    listing off eBay, and that one state is still said in words, because it
 *    is the one a seller has to act on. "Complete" and "Optional" said
 *    nothing a filled or empty field did not already say, and are gone.
 *
 * Opening and closing: a section is open and stays open. Only a `collapsible`
 * section carries a chevron -- the description, whose AI-written body runs
 * to several hundred words, and (from Phase 3) "More options". The folding
 * every card used to offer was a way of coping with ten cards; with none,
 * nothing needs hiding. */
export function Section({ id, title, hint, state, flagged, expand, collapsible = false,
                          defaultOpen = true, className, children }) {
  const [open, setOpen] = useState(defaultOpen);
  const ref = useRef(null);

  // Expanding is state adjusted during render on the transition (React's
  // "adjust state when a prop changes"), so the section is committed already
  // open rather than closed-then-open a frame later. See WorkflowCard's
  // history for why this is a transition and not a level.
  const wantsOpen = !!(flagged || expand);
  const [prevWantsOpen, setPrevWantsOpen] = useState(wantsOpen);
  if (wantsOpen !== prevWantsOpen) {
    setPrevWantsOpen(wantsOpen);
    if (wantsOpen) setOpen(true);
  }

  // Scrolling is a DOM side effect and stays in an effect, firing on mount
  // too when the section starts flagged.
  useEffect(() => {
    if (flagged) ref.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [flagged]);

  const shown = collapsible ? open : true;
  const heading = (
    <>
      <span className="flex-1 min-w-0 flex items-center gap-1.5">
        <span className="font-bold text-[16px] text-ink truncate">{title}</span>
        {hint && <InfoTip text={String(hint)} />}
      </span>
      {state === "attention" && (
        <span
          className="inline-flex items-center gap-1 rounded-full bg-warning-soft border border-warning/40 px-2 py-0.5 text-[11.5px] font-bold text-warning shrink-0"
          title="eBay won't accept the listing until this is fixed"
        >
          <AlertCircle size={12} strokeWidth={2.5} aria-hidden /> Blocks publish
        </span>
      )}
    </>
  );

  return (
    <section
      ref={ref}
      id={`card-${id}`}
      data-section={id}
      className={cn(
        // A rule above every section but the first: the rules are what make
        // one long page scannable without boxes.
        "border-t border-line first:border-t-0 pt-5 first:pt-0 pb-5 last:pb-0",
        "transition-colors duration-200",
        // A refusal rings the section, not a card: the same red, on the
        // left edge, where the eye lands first.
        flagged && "border-l-2 border-l-error pl-4 -ml-4",
        className,
      )}
    >
      {collapsible ? (
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="w-full flex items-center gap-3 text-left cursor-pointer group"
        >
          {heading}
          <motion.span
            animate={{ rotate: open ? 180 : 0 }}
            transition={{ duration: 0.2 }}
            className="text-ink-faint group-hover:text-ink shrink-0"
          >
            <ChevronDown size={18} aria-hidden />
          </motion.span>
        </button>
      ) : (
        <div className="flex items-center gap-3">{heading}</div>
      )}
      <AnimatePresence initial={false}>
        {shown && (
          <motion.div
            initial={collapsible ? { height: 0, opacity: 0 } : false}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: "easeOut" }}
            className={cn(collapsible && "overflow-hidden")}
          >
            <div className="pt-4">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

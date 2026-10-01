import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * One collapsible row of the "More" section. Closed by default: these are
 * the settings a seller touches at setup and rarely after, and keeping them
 * folded is what lets the eBay essentials above read in one screen.
 *
 * A closed row unmounts its children, so the reads they make (the Etsy
 * options, the AI references) stop costing every Settings visit. A deep
 * link into a row opens it: before first paint via `defaultOpen`, or on
 * arrival via `expand`.
 */
export function Disclosure({ id, icon: Icon, title, summary, defaultOpen = false,
                             expand = false, children }) {
  const [open, setOpen] = useState(defaultOpen || expand);
  // `expand` is a deep link arriving after mount. React's documented
  // "adjust state when a prop changes" pattern: react to the transition
  // during render, so the row is committed open rather than opening a
  // frame later. A seller who then folds it keeps it folded.
  const [prevExpand, setPrevExpand] = useState(expand);
  if (expand !== prevExpand) {
    setPrevExpand(expand);
    if (expand) setOpen(true);
  }
  return (
    <section
      id={`settings-${id}`}
      className="scroll-mt-24 border-t border-line first:border-t-0"
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full flex items-center gap-3 py-4 text-left cursor-pointer group"
      >
        {Icon && (
          <span className="grid place-items-center size-9 rounded-[12px] bg-blue-soft text-blue shrink-0">
            <Icon size={18} strokeWidth={2} aria-hidden />
          </span>
        )}
        <span className="flex-1 min-w-0">
          <span className="block font-bold text-[15px] text-ink truncate">{title}</span>
          {summary && (
            <span className="block text-[13px] text-ink-secondary truncate">{summary}</span>
          )}
        </span>
        <motion.span
          animate={{ rotate: open ? 180 : 0 }}
          transition={{ duration: 0.2 }}
          className="text-ink-faint group-hover:text-ink shrink-0"
        >
          <ChevronDown size={19} aria-hidden />
        </motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: "easeOut" }}
            className={cn("overflow-hidden")}
          >
            <div className="pb-6 pt-1">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

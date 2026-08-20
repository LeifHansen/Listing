import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { ProgressChip } from "@/components/ui/badges";
import { InfoTip } from "@/components/ui/fields";

// WorkflowCard — one collapsible step of the listing workflow. Shows a
// ✔ Complete / Needs attention chip and expands itself when eBay flags it.
export function WorkflowCard({ id, icon: Icon, title, hint, state, flagged, children }) {
  const [open, setOpen] = useState(true);
  const ref = useRef(null);

  // When a publish error points here, expand and scroll into view. These are
  // two separate concerns and are handled in two separate places:
  //
  // 1. Expanding is pure state adjustment, so it happens during render using
  //    React's documented "adjust state when a prop changes" pattern — track
  //    the previous `flagged` in state and react to the transition. Doing it
  //    here rather than in an effect avoids a cascading second render (the
  //    card is committed already-expanded instead of collapsed-then-expanded).
  //    Note this only fires on a false → true transition, so a user who
  //    manually collapses a still-flagged card keeps it collapsed — same as
  //    before. PublishCard's "fix this" button deliberately clears fixTarget
  //    and re-sets it on the next frame to force that transition again.
  const [prevFlagged, setPrevFlagged] = useState(flagged);
  if (flagged !== prevFlagged) {
    setPrevFlagged(flagged);
    if (flagged) setOpen(true);
  }

  // 2. Scrolling is a real side effect on the DOM, so it stays in an effect,
  //    with the same `[flagged]` dependency and the same truthiness guard it
  //    always had — including firing on mount when the card starts flagged.
  useEffect(() => {
    if (flagged) {
      ref.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [flagged]);

  return (
    <section
      ref={ref}
      id={`card-${id}`}
      className={cn(
        "bg-card rounded-card border shadow-card transition-colors duration-200",
        flagged ? "border-error" : "border-line",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full flex items-center gap-3 p-5 sm:px-6 text-left cursor-pointer group"
      >
        <span className={cn(
          "grid place-items-center size-10 rounded-[13px] shrink-0 transition-colors duration-200",
          state === "complete" ? "bg-green-soft text-green" : "bg-blue-soft text-blue",
        )}>
          <Icon size={19} strokeWidth={2} aria-hidden />
        </span>
        {/* The explainer line lives behind a hover ⓘ, not a visible subtitle —
            the editor reads as a clean checklist. */}
        <span className="flex-1 min-w-0 flex items-center gap-1.5">
          <span className="font-bold text-[16px] text-ink truncate">{title}</span>
          {hint && <InfoTip text={String(hint)} />}
        </span>
        <ProgressChip state={state} className="shrink-0 hidden sm:inline-flex" />
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
            className="overflow-hidden"
          >
            <div className="px-5 sm:px-6 pb-6 pt-1">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

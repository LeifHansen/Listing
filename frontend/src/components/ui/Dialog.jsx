import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "./Button";

// Every open dialog, bottom to top. Two things need to know which one is on
// top: Escape (only the topmost closes — a confirm over the shipping dialog
// used to take both down with one press) and the page scroll lock (owned by
// the first to open, released by the last to close, rather than each one
// resetting it on its own unmount while another was still up).
const openStack = [];
const lockScroll = () => { document.body.style.overflow = "hidden"; };
const unlockScroll = () => { document.body.style.overflow = ""; };

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), '
  + 'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Dialog — floating card over a soft scrim. Closes on Escape and on backdrop
// click, but only when the press STARTED on the backdrop (a press-drag-release
// gesture that ends over the scrim must not destroy unsaved work).
//
// A modal for everyone, not only the mouse: focus moves into the panel when it
// opens, Tab cycles inside it, and focus goes back to whatever opened it when
// it closes — so a keyboard or screen-reader user is not left on the page
// behind the scrim, able to press the controls the dialog is supposed to be
// in front of. The heading labels the dialog whatever `title` is (a string or
// JSX), through aria-labelledby.
export function Dialog({ open, onClose, title, children, className, wide = false }) {
  const downOnBackdrop = useRef(false);
  const panel = useRef(null);
  const id = useId();
  const headingId = `${id}-title`;

  useEffect(() => {
    if (!open) return;
    const me = { id };
    if (!openStack.length) lockScroll();
    openStack.push(me);
    const opener = document.activeElement;

    // Into the panel, on the next frame: framer-motion mounts the node before
    // the ref settles, and focusing the panel itself (not its first control)
    // keeps a destructive confirm's button from being pressed by the Enter
    // that opened the dialog.
    const raf = requestAnimationFrame(() => panel.current?.focus());

    const onKey = (e) => {
      if (openStack[openStack.length - 1] !== me) return;
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !panel.current) return;
      const items = Array.from(panel.current.querySelectorAll(FOCUSABLE))
        .filter((el) => !el.closest("[hidden]") && el.getAttribute("aria-hidden") !== "true");
      if (!items.length) { e.preventDefault(); panel.current.focus(); return; }
      const first = items[0];
      const last = items[items.length - 1];
      const inside = panel.current.contains(document.activeElement);
      if (e.shiftKey && (document.activeElement === first || !inside)) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !inside)) {
        e.preventDefault(); first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("keydown", onKey);
      const at = openStack.indexOf(me);
      if (at >= 0) openStack.splice(at, 1);
      if (!openStack.length) unlockScroll();
      if (opener && typeof opener.focus === "function" && document.contains(opener)) {
        opener.focus();
      }
    };
  }, [open, onClose, id]);

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          className="fixed inset-0 z-50 grid place-items-center bg-ink/30 backdrop-blur-[2px] p-4 overflow-y-auto"
          onMouseDown={(e) => { downOnBackdrop.current = e.target === e.currentTarget; }}
          onClick={(e) => {
            if (e.target === e.currentTarget && downOnBackdrop.current) onClose();
            downOnBackdrop.current = false;
          }}
        >
          <motion.div
            ref={panel}
            role="dialog"
            aria-modal="true"
            aria-labelledby={headingId}
            tabIndex={-1}
            initial={{ opacity: 0, y: 14, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.98 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className={cn(
              "relative w-full bg-card rounded-card shadow-pop border border-line p-6 my-8 outline-none",
              wide ? "max-w-3xl" : "max-w-md",
              className,
            )}
          >
            <div className="flex items-center justify-between gap-4 mb-4">
              <h2 id={headingId} className="text-lg font-bold text-ink">{title}</h2>
              <Button variant="ghost" size="iconSm" aria-label="Close" onClick={onClose}>
                <X size={18} />
              </Button>
            </div>
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

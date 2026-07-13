import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "./Button";

// Dialog — floating card over a soft scrim. Closes on Escape and on backdrop
// click, but only when the press STARTED on the backdrop (a press-drag-release
// gesture that ends over the scrim must not destroy unsaved work).
export function Dialog({ open, onClose, title, children, className, wide = false }) {
  const downOnBackdrop = useRef(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

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
            role="dialog"
            aria-modal="true"
            aria-label={typeof title === "string" ? title : undefined}
            initial={{ opacity: 0, y: 14, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.98 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className={cn(
              "relative w-full bg-card rounded-card shadow-pop border border-line p-6 my-8",
              wide ? "max-w-3xl" : "max-w-md",
              className,
            )}
          >
            <div className="flex items-center justify-between gap-4 mb-4">
              <h2 className="text-lg font-bold text-ink">{title}</h2>
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

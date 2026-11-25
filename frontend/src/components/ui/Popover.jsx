import { useEffect, useId, useRef, useState } from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { Button } from "./Button";

/* Close-on-outside and close-on-Escape for anything that opens beneath a
 * trigger. Menu has always done this; the Manage tab's category and sort
 * pickers do the same thing with a different body, so the part every popover
 * gets wrong on its own lives in one hook.
 *
 * Escape is stopped from propagating, so a popover inside a Dialog closes the
 * popover and leaves the dialog open. */
export function usePopover(open, setOpen, rootRef) {
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); setOpen(false); }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, setOpen, rootRef]);
}

/* A button that opens a panel of arbitrary content beneath it — a checklist,
 * a small form. For a list of ACTIONS use Menu, which adds the menu roles and
 * arrow-key movement a panel of checkboxes should not have.
 *
 * `trigger` is the button's content; `buttonProps` go to the Button (variant,
 * size, className, aria-label). `align` is which edge the panel hangs from. */
export function Popover({ trigger, label, children, buttonProps = {},
                          align = "left", panelClassName, className }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const id = useId();
  usePopover(open, setOpen, rootRef);
  return (
    <div ref={rootRef} className={cn("relative inline-flex", className)}>
      <Button
        variant="ghost"
        size="sm"
        {...buttonProps}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={buttonProps["aria-label"] || label}
      >
        {trigger}
      </Button>
      {open && (
        <motion.div
          id={id}
          role="dialog"
          aria-label={label}
          initial={{ opacity: 0, y: -4, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.12, ease: "easeOut" }}
          className={cn(
            "absolute top-full mt-1.5 z-40 w-72 max-w-[calc(100vw-2rem)]",
            "bg-card border border-line rounded-card shadow-float p-2",
            align === "right" ? "right-0" : "left-0",
            panelClassName,
          )}
        >
          {typeof children === "function" ? children({ close: () => setOpen(false) }) : children}
        </motion.div>
      )}
    </div>
  );
}

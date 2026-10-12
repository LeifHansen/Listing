import { useEffect, useId, useRef, useState } from "react";
import { motion } from "framer-motion";
import { MoreHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "./Button";

/* Menu — a button that opens a short list of actions beneath it.
 *
 * The editor's header used to carry five buttons (My drafts, Start over,
 * Delete, Exit, Back to batch) and its publish bar four more (Cancel,
 * Delete, Check, Save Draft), most of them pressed once a month. They are
 * one ⋯ now (LISTING_REDESIGN.md, "Header"), and this is the first popover
 * the app has had: Dialog is modal and the notifications bell draws its own
 * panel. What this holds is the part every menu gets wrong on its own:
 *
 *  - it closes on a click anywhere outside it, on Escape, and after an item
 *    is chosen -- a menu that stays open over the form is a modal with no
 *    scrim;
 *  - the list is `role="menu"` with `role="menuitem"` entries, and the
 *    trigger says `aria-expanded` and `aria-haspopup`, so a screen reader
 *    hears a menu and not a column of buttons;
 *  - arrow keys move between items, Home/End jump, and focus lands on the
 *    first item when the menu opens from the keyboard.
 *
 * `items`: [{ label, icon, onSelect, danger, disabled, hint, divider }].
 * An item with `divider` draws a rule above itself. `hint` is a short
 * right-aligned note (a shortcut, a consequence). */
export function Menu({ items, label = "More actions", align = "right", className,
                      size = "icon", variant = "ghost", children }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const id = useId();

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
  }, [open]);

  // Focus the first item once the list is on screen, so the keyboard path
  // (Enter on the trigger) lands somewhere useful.
  useEffect(() => {
    if (!open) return;
    const first = listRef.current?.querySelector('[role="menuitem"]:not([disabled])');
    first?.focus();
  }, [open]);

  const moveFocus = (e) => {
    const items_ = [...(listRef.current?.querySelectorAll('[role="menuitem"]:not([disabled])') || [])];
    if (!items_.length) return;
    const i = items_.indexOf(document.activeElement);
    const go = (n) => { e.preventDefault(); items_[(n + items_.length) % items_.length].focus(); };
    if (e.key === "ArrowDown") go(i + 1);
    else if (e.key === "ArrowUp") go(i - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(items_.length - 1);
  };

  const choose = (item) => {
    setOpen(false);
    item.onSelect?.();
  };

  return (
    <div ref={rootRef} className={cn("relative inline-flex", className)}>
      <Button
        variant={variant}
        size={size}
        onClick={() => setOpen((o) => !o)}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        title={label}
      >
        {children || <MoreHorizontal aria-hidden />}
      </Button>
      {open && (
          <motion.ul
            ref={listRef}
            id={id}
            role="menu"
            aria-label={label}
            onKeyDown={moveFocus}
            initial={{ opacity: 0, y: -4, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.12, ease: "easeOut" }}
            className={cn(
              "absolute top-full mt-1.5 z-40 min-w-56 py-1.5",
              "bg-card border border-line rounded-card shadow-float",
              align === "right" ? "right-0" : "left-0",
            )}
          >
            {items.filter(Boolean).map((item, i) => (
              <li key={item.key || item.label} role="none"
                className={cn(item.divider && i > 0 && "border-t border-line mt-1.5 pt-1.5")}>
                <button
                  type="button"
                  role="menuitem"
                  disabled={item.disabled}
                  title={item.title}
                  onClick={() => choose(item)}
                  className={cn(
                    "w-full flex items-center gap-2.5 px-3.5 py-2 text-left text-[14px] font-semibold",
                    "cursor-pointer transition-colors duration-100 outline-none",
                    "[&_svg]:size-4 [&_svg]:shrink-0",
                    item.danger ? "text-error hover:bg-error-soft focus-visible:bg-error-soft"
                      : "text-ink hover:bg-bg-sunken focus-visible:bg-bg-sunken",
                    item.disabled && "opacity-45 cursor-default hover:bg-transparent",
                  )}
                >
                  {item.icon}
                  <span className="flex-1 min-w-0 truncate">{item.label}</span>
                  {item.hint && (
                    <span className="shrink-0 text-[11.5px] font-medium text-ink-faint">{item.hint}</span>
                  )}
                </button>
              </li>
            ))}
          </motion.ul>
      )}
    </div>
  );
}

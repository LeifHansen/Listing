import { useEffect, useId, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Check, MoreHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "./Button";
import { usePopover } from "./Popover";

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
 * `items`: [{ label, icon, onSelect, danger, disabled, hint, divider,
 * checked, className }].
 * An item with `divider` draws a rule above itself. `hint` is a short
 * right-aligned note (a shortcut, a consequence). `checked` (true/false)
 * makes the item one of a radio set — a sort order, say — read as
 * `menuitemradio` with a tick on the chosen one. `className` lands on the
 * item's <li>, so a menu can hold entries that only exist at one screen size
 * (the Manage bulk bar folds its overflow into More on a phone). */
const ITEM_SELECTOR = '[role="menuitem"]:not([disabled]), [role="menuitemradio"]:not([disabled])';

/* The enabled items actually on screen. An item can be in the DOM and hidden
   at this screen size (its `className`), and focus must skip it. */
function visibleItems(list) {
  return [...(list?.querySelectorAll(ITEM_SELECTOR) || [])].filter((el) => {
    const li = el.closest("li");
    return !li || window.getComputedStyle(li).display !== "none";
  });
}

export function Menu({ items, label = "More actions", className,
                      size = "icon", variant = "ghost", children, disabled,
                      triggerClassName, align = "right" }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const id = useId();

  usePopover(open, setOpen, rootRef);

  // Focus the first item once the list is on screen, so the keyboard path
  // (Enter on the trigger) lands somewhere useful.
  useEffect(() => {
    if (!open) return;
    const first = visibleItems(listRef.current)[0];
    first?.focus();
  }, [open]);

  const moveFocus = (e) => {
    // Visible ones only: an item hidden at this screen size (`className`)
    // is in the DOM but must not take the focus.
    const items_ = visibleItems(listRef.current);
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
        disabled={disabled}
        className={triggerClassName}
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
              align === "left" ? "left-0" : "right-0",
            )}
          >
            {items.filter(Boolean).map((item, i) => (
              <li key={item.key || item.label} role="none"
                className={cn(item.divider && i > 0 && "border-t border-line mt-1.5 pt-1.5",
                  item.className)}>
                <button
                  type="button"
                  role={item.checked == null ? "menuitem" : "menuitemradio"}
                  aria-checked={item.checked == null ? undefined : !!item.checked}
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
                  {item.checked != null && (
                    <Check aria-hidden className={cn("text-blue", !item.checked && "invisible")} />
                  )}
                </button>
              </li>
            ))}
          </motion.ul>
      )}
    </div>
  );
}

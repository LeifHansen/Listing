import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bell, PartyPopper, Truck } from "lucide-react";
import { cn } from "@/lib/utils";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";

// "2h ago" / "3d ago" — coarse on purpose; the exact time is in the tooltip.
function timeAgo(iso) {
  if (!iso) return "";
  const mins = Math.max(0, (Date.now() - Date.parse(iso)) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${Math.floor(mins)}m ago`;
  if (mins < 60 * 24) return `${Math.floor(mins / 60)}h ago`;
  return `${Math.floor(mins / 1440)}d ago`;
}

// NotificationsBell — the TopBar bell: unread badge, dropdown list, and the
// "Ship it" jump into the shipping dialog for sold items. Hidden until the
// user is signed in (notifications are per-account).
export function NotificationsBell() {
  const {
    user, notifications, loadNotifications, markNotificationsRead,
    openShipping, openListing,
  } = useApp();
  const [open, setOpen] = useState(false);
  const panelRef = useRef(null);

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!user) return null;
  const { items, unread } = notifications;

  const toggle = () => {
    setOpen((o) => !o);
    if (!open) loadNotifications();
  };

  return (
    <div className="relative" ref={panelRef}>
      <Button
        variant="ghost"
        size="icon"
        onClick={toggle}
        aria-label={unread ? `Notifications — ${unread} unread` : "Notifications"}
        aria-expanded={open}
        className={cn(unread > 0 && "text-ink")}
      >
        <Bell aria-hidden />
        {unread > 0 && (
          <span
            aria-hidden
            className="absolute top-1.5 right-1.5 grid place-items-center min-w-[18px] h-[18px] px-1 rounded-full bg-error text-white font-display text-[10px] font-bold tabular-nums"
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </Button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: 6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 4, scale: 0.98 }}
            transition={{ duration: 0.15, ease: "easeOut" }}
            className={cn(
              "absolute right-0 top-full mt-2 z-50 w-[22rem] max-w-[calc(100vw-2rem)]",
              "bg-card border border-line rounded-card shadow-pop overflow-hidden",
            )}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-line">
              <span className="font-bold text-sm text-ink">Notifications</span>
              {unread > 0 && (
                <button
                  type="button"
                  onClick={() => markNotificationsRead()}
                  className="text-[12px] font-semibold text-blue cursor-pointer hover:underline"
                >
                  Mark all read
                </button>
              )}
            </div>

            {items.length === 0 ? (
              // "Nothing yet" is a claim about the seller's SALES, on the
              // surface they check to find out whether they owe a buyer a
              // parcel. A read that failed underneath a 200 must not make it.
              <p className="px-4 py-8 text-center text-[13px] text-ink-secondary">
                {notifications.checked === false
                  ? "We couldn’t load your notifications just now — this doesn’t "
                    + "mean nothing has sold. We’ll try again in a moment."
                  : "Nothing yet — when an item sells, it lands here so you can ship it fast."}
              </p>
            ) : (
              <ul className="max-h-[24rem] overflow-y-auto divide-y divide-line">
                {items.map((n) => (
                  <li
                    key={n.id}
                    className={cn("flex gap-3 px-4 py-3", !n.read && "bg-blue-soft/40")}
                  >
                    <span className={cn(
                      "grid place-items-center size-9 rounded-[12px] shrink-0 mt-0.5",
                      n.kind === "sold" ? "bg-green-soft text-green" : "bg-blue-soft text-blue",
                    )}>
                      <PartyPopper size={17} aria-hidden />
                    </span>
                    <div className="min-w-0 flex-1">
                      <button
                        type="button"
                        className="block w-full text-left cursor-pointer"
                        onClick={() => {
                          markNotificationsRead([n.id]);
                          if (n.listing_id) { openListing(n.listing_id); setOpen(false); }
                        }}
                      >
                        <span className="block font-semibold text-[13px] text-ink truncate">
                          {n.title}
                        </span>
                        <span className="block text-[12px] text-ink-secondary leading-snug mt-0.5">
                          {n.body}
                        </span>
                      </button>
                      <div className="mt-1.5 flex items-center gap-2">
                        <span className="text-[11px] text-ink-faint"
                          title={n.created_at || undefined}>
                          {timeAgo(n.created_at)}
                        </span>
                        {n.kind === "sold" && (
                          <Button
                            variant="soft" size="sm" className="h-7 px-2.5 text-[12px]"
                            onClick={() => {
                              markNotificationsRead([n.id]);
                              setOpen(false);
                              openShipping(n.listing_id || null);
                            }}
                          >
                            <Truck aria-hidden /> Ship it
                          </Button>
                        )}
                      </div>
                    </div>
                    {!n.read && (
                      <span aria-hidden className="size-2 rounded-full bg-blue shrink-0 mt-2" />
                    )}
                  </li>
                ))}
              </ul>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

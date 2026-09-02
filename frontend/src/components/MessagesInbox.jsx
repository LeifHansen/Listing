import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { MessageCircle, Store } from "lucide-react";
import { cn, timeAgo } from "@/lib/utils";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";

// Initials for the avatar disc. eBay usernames are the only name we get, so
// the first two characters are the most identity available.
function initials(name) {
  const s = (name || "").replace(/[^a-z0-9]/gi, "");
  return (s.slice(0, 2) || "??").toUpperCase();
}

// One conversation row: who, what they said, when, and whether it's waiting.
export function ConversationRow({ conversation: c, active, showSource, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "true" : undefined}
      className={cn(
        "flex gap-3 w-full text-left px-4 py-3 cursor-pointer transition-colors duration-150",
        active ? "bg-blue-soft" : "hover:bg-bg-sunken",
      )}
    >
      <span
        aria-hidden
        className="grid place-items-center size-10 shrink-0 rounded-full bg-blue-soft text-blue font-display font-bold text-[13px]"
      >
        {initials(c.counterparty)}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline gap-2">
          <span className={cn(
            "truncate text-[14px] text-ink",
            c.unread > 0 ? "font-bold" : "font-semibold",
          )}>
            {c.counterparty || "Unknown buyer"}
          </span>
          <span className="ml-auto shrink-0 text-[11px] text-ink-faint"
            title={c.last_at || undefined}>
            {timeAgo(c.last_at)}
          </span>
        </span>
        <span className="block text-[12px] text-ink-secondary leading-snug line-clamp-2 mt-0.5">
          {c.snippet || "No messages yet"}
        </span>
        {/* eBay threads are per-buyer PER ITEM, so without the listing one
            buyer asking about two things reads as two identical rows. */}
        {c.title && (
          <span className="block truncate text-[11px] text-ink-faint mt-0.5">
            {c.title}
          </span>
        )}
        {showSource && c.marketplace_label && (
          <span className="inline-flex items-center gap-1 mt-1 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">
            <Store size={10} aria-hidden /> {c.marketplace_label}
          </span>
        )}
      </span>
      {c.unread > 0 && (
        <>
          <span aria-hidden className="size-2 rounded-full bg-blue shrink-0 mt-3" />
          <span className="sr-only">Unread.</span>
        </>
      )}
    </button>
  );
}

// What to say when there is nothing to show — the reason matters more than
// the emptiness. "Reconnect eBay" is actionable; "no messages" isn't.
export function inboxEmptyCopy(messages) {
  const { reason, message, sources } = messages;
  if (reason === "not_connected" || reason === "disabled") {
    return message || "Connect a marketplace to see buyer messages here.";
  }
  if (reason === "needs_reconnect") {
    return message || "Reconnect in Settings to grant message access.";
  }
  if (reason === "error") {
    return message || "Couldn't reach your marketplaces just now.";
  }
  const live = (sources || []).filter((s) => s.available).length;
  return live
    ? "No buyer messages yet. Questions from buyers land here — never eBay's own mail."
    : "No marketplace is connected for messages yet.";
}

// MessagesInbox — the TopBar inbox: unread badge, recent conversations, and a
// way into the full Messages screen. Person-to-person only; the bell next door
// is where the app's own alerts live.
//
// Hidden entirely when messaging is off. A permanently dead icon is worse
// than no icon, and `messaging_enabled` rides /api/ebay/status, which loads at
// boot — so it never flashes in and back out.
export function MessagesInbox() {
  const { user, ebay, messages, loadMessages, openMessages, setView } = useApp();
  const [open, setOpen] = useState(false);
  const panelRef = useRef(null);

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

  if (!user || !ebay.messaging_enabled) return null;
  const { conversations, unread, sources } = messages;
  const multi = (sources || []).filter((s) => s.available).length > 1;

  const toggle = () => {
    setOpen((o) => !o);
    if (!open) loadMessages();
  };

  return (
    <div className="relative" ref={panelRef}>
      <Button
        variant="ghost"
        size="icon"
        onClick={toggle}
        aria-label={unread ? `Messages — ${unread} unread` : "Messages"}
        aria-expanded={open}
        className={cn(unread > 0 && "text-ink")}
      >
        <MessageCircle aria-hidden />
        {unread > 0 && (
          // Blue, not the bell's red: red here means "act now, an item sold",
          // and two red badges side by side are indistinguishable anyway.
          <span
            aria-hidden
            className="absolute top-1.5 right-1.5 grid place-items-center min-w-[18px] h-[18px] px-1 rounded-full bg-blue text-on-accent font-display text-[10px] font-bold tabular-nums"
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
            aria-label="Recent messages"
            className={cn(
              "absolute right-0 top-full mt-2 z-50 w-[22rem] max-w-[calc(100vw-2rem)]",
              "bg-card border border-line rounded-card shadow-pop overflow-hidden",
            )}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-line">
              <span className="font-bold text-sm text-ink">Messages</span>
              <span className="text-[11px] text-ink-faint">From buyers</span>
            </div>

            {conversations.length === 0 ? (
              <div className="px-4 py-8 text-center">
                <p className="text-[13px] text-ink-secondary">
                  {inboxEmptyCopy(messages)}
                </p>
                {messages.reason === "not_connected" && (
                  <Button variant="soft" size="sm" className="mt-3"
                    onClick={() => { setOpen(false); setView("settings"); }}>
                    Connect a marketplace
                  </Button>
                )}
              </div>
            ) : (
              <ul className="max-h-[24rem] overflow-y-auto divide-y divide-line">
                {conversations.slice(0, 8).map((c) => (
                  <li key={c.id}>
                    <ConversationRow
                      conversation={c}
                      showSource={multi}
                      onClick={() => { setOpen(false); openMessages(c.id); }}
                    />
                  </li>
                ))}
              </ul>
            )}

            <div className="border-t border-line p-2">
              <Button variant="ghost" size="sm" className="w-full justify-center"
                onClick={() => { setOpen(false); openMessages(); }}>
                See all messages
              </Button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

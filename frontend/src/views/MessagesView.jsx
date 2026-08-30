import { useMemo } from "react";
import { motion } from "framer-motion";
import { MessageCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { ConversationRow, inboxEmptyCopy } from "@/components/MessagesInbox";
import { SourceTabs } from "@/views/messages/SourceTabs";
import { MessageThread } from "@/views/messages/MessageThread";
import { Composer } from "@/views/messages/Composer";

// MessagesView — the full inbox: conversations on the left, the thread on the
// right, one column on mobile. Buyer messages only; the app's own alerts stay
// in the bell where a seller can triage them separately.
export function MessagesView() {
  const {
    user, ebay, messages, threads, activeConversationId, openConversation,
    sendMessage, messageSource, setMessageSource, openAuth, openListing, setView,
  } = useApp();

  const unreadBySource = useMemo(() => {
    const out = {};
    for (const s of messages.sources || []) out[s.key] = s.unread || 0;
    return out;
  }, [messages.sources]);

  const shown = useMemo(() => (
    messageSource
      ? messages.conversations.filter((c) => c.marketplace === messageSource)
      : messages.conversations
  ), [messages.conversations, messageSource]);

  const multi = (messages.sources || []).filter((s) => s.available).length > 1;
  const active = shown.find((c) => c.id === activeConversationId)
    || messages.conversations.find((c) => c.id === activeConversationId);
  const thread = threads[activeConversationId];
  // Prefer the thread's own copy: it's the freshest, and it survives the
  // conversation dropping out of the filtered list.
  const header = thread?.conversation || active;

  if (!user) {
    return (
      <div className="grid place-items-center py-24 text-center">
        <div>
          <MessageCircle size={32} className="mx-auto text-ink-faint" aria-hidden />
          <p className="mt-3 text-[14px] text-ink-secondary">
            Log in to see your buyer messages.
          </p>
          <Button variant="primary" size="md" className="mt-4" onClick={() => openAuth()}>
            Log in
          </Button>
        </div>
      </div>
    );
  }

  if (!ebay.messaging_enabled) {
    return (
      <div className="grid place-items-center py-24 text-center">
        <div className="max-w-sm">
          <MessageCircle size={32} className="mx-auto text-ink-faint" aria-hidden />
          <p className="mt-3 text-[14px] text-ink-secondary">
            Buyer messages aren't switched on for this app yet.
          </p>
        </div>
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      className="flex flex-col gap-3"
    >
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="font-display font-bold text-[22px] text-ink">Messages</h1>
        <span className="text-[12px] text-ink-secondary">
          Questions from buyers — never marketplace system mail.
        </span>
        <div className="ml-auto">
          <SourceTabs
            sources={messages.sources}
            value={messageSource}
            onChange={setMessageSource}
            unreadBySource={unreadBySource}
          />
        </div>
      </div>

      <div className={cn(
        "bg-card border border-line rounded-card shadow-card overflow-hidden",
        "md:grid md:grid-cols-[20rem_1fr]",
        "h-[calc(100dvh-14rem)] min-h-[24rem]",
      )}>
        {/* Conversations. On mobile this is the whole screen until one opens. */}
        <div className={cn(
          "md:border-r border-line overflow-y-auto h-full",
          activeConversationId && "hidden md:block",
        )}>
          {shown.length === 0 ? (
            <div className="px-4 py-10 text-center">
              <p className="text-[13px] text-ink-secondary">
                {inboxEmptyCopy(messages)}
              </p>
              {messages.reason === "not_connected" && (
                <Button variant="soft" size="sm" className="mt-3"
                  onClick={() => setView("settings")}>
                  Connect a marketplace
                </Button>
              )}
            </div>
          ) : (
            <ul className="divide-y divide-line">
              {shown.map((c) => (
                <li key={c.id}>
                  <ConversationRow
                    conversation={c}
                    active={c.id === activeConversationId}
                    showSource={multi}
                    onClick={() => openConversation(c.id)}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Thread. Hidden on mobile until a conversation is picked. */}
        <div className={cn(
          "h-full min-h-0 flex-col",
          activeConversationId ? "flex" : "hidden md:flex",
        )}>
          <MessageThread
            conversation={header}
            thread={thread}
            onBack={() => openConversation(null)}
            onOpenListing={(c) => c.listing_record_id && openListing(c.listing_record_id)}
          />
          {header && (
            <Composer
              disabled={!messages.available}
              disabledNote={messages.message}
              onSend={(text) => sendMessage(activeConversationId, text)}
            />
          )}
        </div>
      </div>
    </motion.div>
  );
}

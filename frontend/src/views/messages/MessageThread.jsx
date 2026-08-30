import { useEffect, useRef } from "react";
import { ChevronLeft, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/Button";

function dayLabel(iso) {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const d = new Date(t);
  const today = new Date();
  const sameDay = (a, b) => a.toDateString() === b.toDateString();
  if (sameDay(d, today)) return "Today";
  const yest = new Date(today);
  yest.setDate(today.getDate() - 1);
  if (sameDay(d, yest)) return "Yesterday";
  return d.toLocaleDateString(undefined,
    { month: "short", day: "numeric", year: d.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}

function clockTime(iso) {
  const t = Date.parse(iso);
  return Number.isFinite(t)
    ? new Date(t).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : "";
}

// MessageThread — the conversation itself. Bubbles, day dividers, and grouping
// of consecutive messages from the same person, the way a phone shows them.
export function MessageThread({ conversation, thread, onBack, onOpenListing }) {
  const endRef = useRef(null);
  const scrollRef = useRef(null);
  const msgs = thread?.messages || [];
  const count = msgs.length;

  useEffect(() => {
    // Jump to newest — but only when the reader is already near the bottom, so
    // scrolling back through history doesn't get yanked away mid-read.
    const box = scrollRef.current;
    if (!box) return;
    const nearBottom =
      box.scrollHeight - box.scrollTop - box.clientHeight < 160;
    if (nearBottom || count <= 1) {
      endRef.current?.scrollIntoView({ block: "end" });
    }
  }, [count]);

  if (!conversation) {
    return (
      <div className="grid place-items-center h-full p-8 text-center">
        <p className="text-[13px] text-ink-secondary">
          Pick a conversation to read it.
        </p>
      </div>
    );
  }

  // Day dividers and run-ends are decided in one pass BEFORE render: deriving
  // them inside the map would mean carrying state across a render callback,
  // which the compiler rightly refuses.
  const rows = msgs.map((m, i) => {
    const day = dayLabel(m.sent_at);
    const prevDay = i > 0 ? dayLabel(msgs[i - 1].sent_at) : "";
    const next = msgs[i + 1];
    return {
      m,
      showDay: !!day && day !== prevDay,
      day,
      // Last of a run from the same speaker — where the clock goes.
      endsRun: !next || next.from_me !== m.from_me,
    };
  });

  return (
    <div className="flex flex-col h-full min-h-0">
      <header className="flex items-center gap-2 px-4 py-3 border-b border-line shrink-0">
        <Button variant="ghost" size="iconSm" onClick={onBack}
          className="md:hidden" aria-label="Back to conversations">
          <ChevronLeft aria-hidden />
        </Button>
        <div className="min-w-0">
          <p className="font-bold text-[15px] text-ink truncate">
            {conversation.counterparty || "Unknown buyer"}
          </p>
          {conversation.title && (
            <button
              type="button"
              onClick={() => onOpenListing?.(conversation)}
              className="text-[12px] text-blue truncate hover:underline cursor-pointer"
            >
              {conversation.title}
            </button>
          )}
        </div>
        {conversation.marketplace_label && (
          <span className="ml-auto shrink-0 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">
            {conversation.marketplace_label}
          </span>
        )}
      </header>

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-4 py-3">
        {thread?.loading && !count && (
          <p className="text-center text-[13px] text-ink-faint py-8">Loading…</p>
        )}
        {thread?.error && !count && (
          <p className="text-center text-[13px] text-error py-8">{thread.error}</p>
        )}
        {!thread?.loading && !thread?.error && !count && (
          <p className="text-center text-[13px] text-ink-secondary py-8">
            No messages in this conversation yet.
          </p>
        )}

        <ol aria-label={`Conversation with ${conversation.counterparty || "buyer"}`}
          className="flex flex-col gap-1">
          {rows.map(({ m, showDay, day, endsRun }, i) => (
              <li key={m.id || i} className="contents">
                {showDay && (
                  <div className="text-center text-[11px] text-ink-faint py-2">
                    {day}
                  </div>
                )}
                <div className={cn("flex flex-col", m.from_me ? "items-end" : "items-start")}>
                  {/* Direction must survive without colour. */}
                  <span className="sr-only">
                    {m.from_me ? "You:" : `${m.author || "Buyer"}:`}
                  </span>
                  <div className={cn(
                    "max-w-[75%] px-3.5 py-2 text-[14px] whitespace-pre-wrap break-words",
                    "rounded-[18px]",
                    m.from_me
                      ? "bg-blue text-on-accent rounded-br-[6px]"
                      : "bg-bg-sunken text-ink rounded-bl-[6px]",
                    m.pending && "opacity-60",
                    m.failed && "bg-error-soft text-error",
                  )}>
                    {m.text}
                  </div>
                  {m.failed && (
                    <span className="inline-flex items-center gap-1 text-[11px] text-error mt-0.5">
                      <AlertCircle size={11} aria-hidden /> Not sent
                    </span>
                  )}
                  {endsRun && !m.pending && !m.failed && m.sent_at && (
                    <span className="text-[10px] text-ink-faint mt-0.5 mb-1">
                      {clockTime(m.sent_at)}
                    </span>
                  )}
                </div>
              </li>
          ))}
        </ol>
        <div ref={endRef} />
      </div>
    </div>
  );
}

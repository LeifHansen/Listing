import { useRef, useState } from "react";
import { ArrowUp } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/Button";

// Composer — Enter sends, Shift+Enter makes a new line, and the box grows with
// the message. On failure the text stays put: losing what someone typed is
// worse than any error message.
export function Composer({ disabled, disabledNote, onSend }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const ref = useRef(null);

  const grow = (el) => {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  };

  const submit = async (e) => {
    e?.preventDefault?.();
    const body = text.trim();
    if (!body || busy || disabled) return;
    setBusy(true);
    const ok = await onSend(body);
    setBusy(false);
    if (ok) {
      setText("");
      if (ref.current) { ref.current.style.height = "auto"; ref.current.focus(); }
    }
  };

  if (disabled) {
    return (
      <div className="border-t border-line px-4 py-3 shrink-0">
        <p className="text-[12px] text-ink-secondary text-center">
          {disabledNote || "You can't reply to this conversation right now."}
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={submit}
      className="flex items-end gap-2 border-t border-line px-3 py-2.5 shrink-0">
      <label className="sr-only" htmlFor="composer">Write a reply</label>
      <textarea
        id="composer"
        ref={ref}
        rows={1}
        value={text}
        placeholder="Message"
        onChange={(e) => { setText(e.target.value); grow(e.target); }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
        }}
        className={cn(
          "flex-1 resize-none max-h-[140px] px-4 py-2.5 text-[14px]",
          "bg-bg-sunken border border-line rounded-[20px] leading-snug",
          "placeholder:text-ink-faint transition-all duration-150",
          "focus:border-blue focus:outline-none focus:ring-2 focus:ring-blue/25",
        )}
      />
      <Button type="submit" variant="primary" size="icon" loading={busy}
        disabled={!text.trim()} aria-label="Send message"
        className="rounded-full shrink-0">
        <ArrowUp aria-hidden />
      </Button>
    </form>
  );
}

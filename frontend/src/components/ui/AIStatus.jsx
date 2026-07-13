import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { Card } from "./Card";

// AIStatusCard — shown while the AI works. Never a plain spinner: an animated
// sparkle, a shimmer, and rotating friendly messages make waiting feel magical.
export function AIStatusCard({ messages, className }) {
  const list = Array.isArray(messages) ? messages : [messages || "Working some magic…"];
  const [i, setI] = useState(0);

  useEffect(() => {
    if (list.length <= 1) return;
    const t = setInterval(() => setI((n) => (n + 1) % list.length), 2200);
    return () => clearInterval(t);
  }, [list.length]);

  return (
    <Card
      role="status"
      aria-live="polite"
      className={cn("flex items-center gap-4 border-blue/20", className)}
    >
      <span className="grid place-items-center size-11 rounded-[14px] bg-blue-soft text-blue shrink-0">
        <Sparkles size={22} className="ai-sparkle" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <AnimatePresence mode="wait">
          <motion.p
            key={list[i % list.length]}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.25 }}
            className="ai-shimmer-text font-semibold text-[15px] truncate"
          >
            {list[i % list.length]}
          </motion.p>
        </AnimatePresence>
        <div className="mt-2 h-1.5 rounded-full ai-shimmer w-full max-w-64" />
      </div>
    </Card>
  );
}

// Small inline variant for tight spots (buttons rows, card headers).
export function AIStatusInline({ message, className }) {
  return (
    <span role="status" aria-live="polite" className={cn("inline-flex items-center gap-2", className)}>
      <Sparkles size={16} className="ai-sparkle text-blue" aria-hidden />
      <span className="ai-shimmer-text text-sm font-semibold">{message}</span>
    </span>
  );
}

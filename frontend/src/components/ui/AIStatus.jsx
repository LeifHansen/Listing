import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { Card } from "./Card";
import { BrandMark } from "@/components/BrandMark";

// AIStatusCard — the branded wait state (publishing, saving drafts, AI work).
// Never a plain spinner: the Thryft Shop mark gently pulsing, a shimmer, and
// rotating friendly messages make waiting feel magical.
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
      <span className="ai-sparkle shrink-0" aria-hidden>
        <BrandMark className="size-11 rounded-[14px]" />
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

// Full-screen branded wait state — used for publish/save so the feedback is
// unmissable no matter where the page is scrolled (the trigger buttons live
// at the bottom of a long form).
export function LoadingOverlay({ messages }) {
  const list = Array.isArray(messages) ? messages : [messages || "Working…"];
  const [i, setI] = useState(0);

  useEffect(() => {
    if (list.length <= 1) return;
    const t = setInterval(() => setI((n) => (n + 1) % list.length), 2200);
    return () => clearInterval(t);
  }, [list.length]);

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-0 z-50 grid place-items-center bg-bg/80 backdrop-blur-sm"
    >
      <div className="flex flex-col items-center gap-5 p-8 text-center">
        <span className="ai-sparkle" aria-hidden>
          <BrandMark className="size-20 rounded-[24px]" />
        </span>
        <AnimatePresence mode="wait">
          <motion.p
            key={list[i % list.length]}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.25 }}
            className="ai-shimmer-text font-bold text-lg"
          >
            {list[i % list.length]}
          </motion.p>
        </AnimatePresence>
        <div className="h-1.5 rounded-full ai-shimmer w-56" aria-hidden />
      </div>
    </div>
  );
}

// BrandPulse — the wait state for a screen that is nothing BUT waiting: the
// upload pipeline, where the seller has handed over their photos and there is
// no form under it yet. The Thryft Shop mark, large and breathing, with the
// current stage in words beneath it. The seller asked for this in place of
// the small status card over a stack of grey skeleton bars, which read as a
// page that had failed to load rather than one that was working.
export function BrandPulse({ message, detail, className }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn("flex flex-col items-center gap-6 py-12 text-center", className)}
    >
      <span className="relative grid place-items-center" aria-hidden>
        <span className="brand-pulse-halo absolute inset-[-40%] rounded-full" />
        {/* No shadow: box-shadow follows the element's BOX, not the logo's
            transparent edges, so it drew a dark rounded square around the
            mark — read as a card the logo was sitting on, which is exactly
            what this screen should not have. The halo is the depth here. */}
        <BrandMark className="brand-pulse relative size-28 rounded-[32px]" />
      </span>
      <div className="flex flex-col items-center gap-1.5 min-w-0 max-w-md">
        <AnimatePresence mode="wait">
          <motion.p
            key={message}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.25 }}
            className="ai-shimmer-text font-bold text-lg"
          >
            {message}
          </motion.p>
        </AnimatePresence>
        {detail && (
          <p className="text-sm text-ink-secondary">{detail}</p>
        )}
      </div>
    </div>
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

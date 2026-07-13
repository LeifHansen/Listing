import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, ExternalLink, Sparkles } from "lucide-react";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";

/* Full-screen moments for the highest-stakes action in the app:
   - while eBay is processing: an unmissable "publishing…" screen
   - when the listing goes live: a big success screen with the eBay link */

const LIVE_MESSAGES = [
  "Pushing your listing to eBay…",
  "Uploading photos…",
  "Creating the offer…",
  "Waiting for eBay to confirm…",
];
const DRAFT_MESSAGES = ["Saving your draft…", "Staging it on eBay…"];

function Scrim({ children, onClick }) {
  return createPortal(
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      className="fixed inset-0 z-[70] grid place-items-center bg-ink/40 backdrop-blur-[3px] p-6"
      onClick={onClick}
    >
      {children}
    </motion.div>,
    document.body,
  );
}

export function PublishOverlay({ w }) {
  const { ebay } = useApp();
  const [msgIdx, setMsgIdx] = useState(0);
  const messages = w.publishing === "live" ? LIVE_MESSAGES : DRAFT_MESSAGES;

  useEffect(() => {
    if (!w.publishing) { setMsgIdx(0); return; }
    const t = setInterval(() => setMsgIdx((i) => (i + 1) % messages.length), 2400);
    return () => clearInterval(t);
  }, [w.publishing, messages.length]);

  const itemUrl = w.celebration?.listingId
    ? `${ebay.env === "sandbox" ? "https://sandbox.ebay.com" : "https://www.ebay.com"}/itm/${w.celebration.listingId}`
    : null;

  return (
    <AnimatePresence>
      {w.publishing && (
        <Scrim key="publishing">
          <motion.div
            role="alert"
            aria-busy="true"
            initial={{ opacity: 0, scale: 0.95, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97 }}
            className="bg-card rounded-card shadow-pop border border-line p-8 w-full max-w-sm text-center"
          >
            <span className="mx-auto grid place-items-center size-16 rounded-[20px] bg-blue-soft text-blue">
              <Sparkles size={30} className="ai-sparkle" aria-hidden />
            </span>
            <AnimatePresence mode="wait">
              <motion.p
                key={messages[msgIdx]}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.25 }}
                className="mt-5 text-lg font-bold text-ink"
              >
                {messages[msgIdx]}
              </motion.p>
            </AnimatePresence>
            <div className="mt-4 h-1.5 rounded-full ai-shimmer" />
            <p className="mt-4 text-[13px] text-ink-secondary">
              Hang tight — eBay usually takes a few seconds.
            </p>
          </motion.div>
        </Scrim>
      )}

      {!w.publishing && w.celebration && (
        <Scrim key="celebration" onClick={w.finishCelebration}>
          <motion.div
            role="dialog"
            aria-label="Listing published"
            initial={{ opacity: 0, scale: 0.9, y: 16 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96 }}
            transition={{ type: "spring", stiffness: 320, damping: 24 }}
            className="bg-card rounded-card shadow-pop border border-line p-8 w-full max-w-md text-center"
            onClick={(e) => e.stopPropagation()}
          >
            <motion.span
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{ type: "spring", stiffness: 380, damping: 15, delay: 0.1 }}
              className="mx-auto grid place-items-center size-20 rounded-full bg-success-soft text-success"
            >
              <CheckCircle2 size={44} strokeWidth={2.2} aria-hidden />
            </motion.span>
            <h2 className="mt-5 text-2xl font-bold text-ink">It's live on eBay! 🎉</h2>
            <p className="mt-2 text-sm text-ink-secondary">
              {w.celebration.listingId
                ? <>Listing ID <strong className="text-ink">{w.celebration.listingId}</strong> — find it under Selling → Active.</>
                : "Find it under Selling → Active in your eBay account."}
            </p>
            <div className="mt-6 flex flex-wrap justify-center gap-2.5">
              {itemUrl && (
                <Button variant="secondary" size="lg" onClick={() => window.open(itemUrl, "_blank", "noopener")}>
                  <ExternalLink aria-hidden /> View on eBay
                </Button>
              )}
              <Button variant="primary" size="lg" onClick={w.finishCelebration}>
                Done
              </Button>
            </div>
          </motion.div>
        </Scrim>
      )}
    </AnimatePresence>
  );
}

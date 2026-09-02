import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { PartyPopper, Rocket } from "lucide-react";

/* What a draft DOES when it goes live.
 *
 * Publishing already flips the record's status, which is what takes the card
 * out of the drafts grid — but it did it in the same frame the server
 * answered, so on a bulk run the grid silently shrank under the seller's
 * cursor and there was no moment that said "that one made it". What was left
 * at the end (the drafts eBay refused, the ones still missing a field) looked
 * identical to what a run that published nothing would leave.
 *
 * So a published card now takes a beat on its way out: a burst of confetti
 * and a "Live on eBay!" stamp, then it lifts off the grid and the rest close
 * the gap. The card is only VISUALLY held — the record is patched the instant
 * the server confirms it, exactly as before, so nothing here can leave a live
 * listing sitting in Drafts if the animation is interrupted.
 *
 * Shared by the drafts strip and the bulk queue: both publish batches, and a
 * seller who learns the animation on one screen must not have to learn a
 * different ending on the other. */

// How long the burst holds before the card lifts away, and how long the
// lift-off itself takes. Both are read by the hook's timers AND by the
// motion transitions below, so the card can never be dropped from the grid
// part-way through its own exit.
export const CELEBRATE_HOLD_MS = 950;
export const CELEBRATE_EXIT_MS = 460;

// The logo palette, as confetti. Fixed at module scope rather than randomized
// per render: a re-render mid-burst (a listings refresh, a keystroke in a
// sibling card) would otherwise re-roll every particle mid-flight.
const CONFETTI = Array.from({ length: 14 }, (_, i) => {
  const angle = (i / 14) * Math.PI * 2 + (i % 3) * 0.21;
  const distance = 44 + (i % 4) * 15;
  return {
    id: i,
    x: Math.round(Math.cos(angle) * distance),
    y: Math.round(Math.sin(angle) * distance) - 6,   // biased up: it's a lift-off
    color: ["var(--brand-red)", "var(--brand-yellow)",
            "var(--brand-green)", "var(--brand-blue)"][i % 4],
    spin: (i % 2 ? 1 : -1) * (120 + i * 24),
    delay: (i % 5) * 0.035,
    round: i % 3 === 0,
  };
});

/**
 * The cards that are mid-celebration, and the ones that have finished it.
 *
 * `celebrate(id, item, index)` starts the two-phase send-off for one card:
 * "burst" while the confetti plays, then "leaving" while it lifts off, then
 * gone. Callers read `celebrating[id]` to know which phase a card is in and
 * `departed` to know it is finished with (the bulk queue keeps its items
 * forever, so that set is what takes a published one off the screen).
 *
 * Every timer is tracked and cleared on unmount — a batch publish whose
 * screen is closed part-way must not set state on a dead component.
 */
export function usePublishCelebration() {
  // id -> { id, item, index, phase }
  const [celebrating, setCelebrating] = useState({});
  const [departed, setDeparted] = useState(() => new Set());
  const timers = useRef(new Map());

  useEffect(() => {
    const running = timers.current;
    return () => {
      running.forEach((ids) => ids.forEach((t) => clearTimeout(t)));
      running.clear();
    };
  }, []);

  const celebrate = useCallback((id, item, index = -1) => {
    // Already on its way out — a second publish of the same card (a retry
    // that raced the first answer) must not restart the burst.
    if (!id || timers.current.has(id)) return;
    setCelebrating((cur) => ({ ...cur, [id]: { id, item, index, phase: "burst" } }));
    const lift = setTimeout(() => {
      setCelebrating((cur) => (cur[id]
        ? { ...cur, [id]: { ...cur[id], phase: "leaving" } } : cur));
    }, CELEBRATE_HOLD_MS);
    const gone = setTimeout(() => {
      timers.current.delete(id);
      setDeparted((cur) => {
        const next = new Set(cur);
        next.add(id);
        return next;
      });
      setCelebrating((cur) => {
        if (!cur[id]) return cur;
        const next = { ...cur };
        delete next[id];
        return next;
      });
    }, CELEBRATE_HOLD_MS + CELEBRATE_EXIT_MS);
    timers.current.set(id, [lift, gone]);
  }, []);

  return { celebrating, departed, celebrate };
}

/**
 * The drafts grid, plus any card that has already left it but is still
 * playing its send-off.
 *
 * Publishing patches the record to "published" immediately, so the card is
 * out of `drafts` within the same frame — this splices the snapshot back in
 * at the position it held, so the animation plays where the seller last saw
 * the card instead of it vanishing and something else jumping into its place.
 * A card still present in `drafts` is never duplicated: the live row wins.
 */
export function withCelebrating(drafts, celebrating) {
  const ghosts = Object.values(celebrating || {})
    .filter((g) => g.item && !drafts.some((d) => d.id === g.id))
    .sort((a, b) => a.index - b.index);
  if (!ghosts.length) return drafts;
  const out = drafts.slice();
  for (const g of ghosts) {
    const at = g.index >= 0 && g.index <= out.length ? g.index : out.length;
    out.splice(at, 0, g.item);
  }
  return out;
}

/**
 * How one card in a publishing grid should be animating right now.
 *
 * `phase` is undefined for an ordinary card (the existing fade-and-rise on
 * mount, staggered by `index`), "burst" while it celebrates, "leaving" while
 * it lifts off. Reduced motion keeps every state — a seller who asked for no
 * animation still needs to see the card acknowledged and then go — and
 * expresses them as opacity alone.
 */
export function publishedCardMotion(phase, { reduced = false, index = 0 } = {}) {
  if (phase === "burst") {
    return {
      animate: reduced
        ? { opacity: 1, y: 0, scale: 1, rotate: 0 }
        : { opacity: 1, y: -6, scale: 1.04, rotate: 0 },
      transition: { type: "spring", stiffness: 340, damping: 17 },
    };
  }
  if (phase === "leaving") {
    return {
      animate: reduced
        ? { opacity: 0, y: 0, scale: 1, rotate: 0 }
        : { opacity: 0, y: -56, scale: 0.55, rotate: -5 },
      transition: {
        duration: CELEBRATE_EXIT_MS / 1000,
        ease: [0.55, 0, 0.85, 0.35],
      },
    };
  }
  return {
    animate: { opacity: 1, y: 0, scale: 1, rotate: 0 },
    transition: { duration: 0.22, delay: Math.min(index * 0.03, 0.3) },
  };
}

/**
 * What the stamp on the card should say.
 *
 * "Live on eBay!" is a claim about where the listing landed, and a publish
 * can now fan out to Etsy and friends (see publishShared.usePublishTargets)
 * — including to Etsy ALONE. `effectiveTargets` is null for the eBay-only
 * sellers who are most of them, which is the one case that can name a
 * marketplace.
 */
export function liveLabel(effectiveTargets) {
  const ebayOnly = !effectiveTargets
    || (effectiveTargets.length === 1 && effectiveTargets[0] === "ebay");
  return ebayOnly ? "Live on eBay!" : "It's live!";
}

/**
 * The send-off itself, laid over the card it belongs to.
 *
 * Announced (role="status") as well as drawn: the whole point is to tell the
 * seller which of a batch made it, and a burst of confetti tells a screen
 * reader nothing. `pointer-events-none` throughout — the card underneath is
 * leaving, and a click landing on it on the way out would open a listing the
 * seller never chose.
 */
export function PublishedBurst({ label = "Live on eBay!", reduced = false }) {
  return (
    <div
      className="absolute inset-0 z-20 grid place-items-center overflow-hidden
        rounded-card pointer-events-none"
      role="status"
    >
      <motion.div
        className="absolute inset-0 rounded-card bg-success-soft/85 backdrop-blur-[1px]"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.18 }}
      />
      {!reduced && CONFETTI.map((c) => (
        <motion.span
          key={c.id}
          aria-hidden
          className="absolute size-2"
          style={{
            background: c.color,
            borderRadius: c.round ? "9999px" : "2px",
          }}
          initial={{ x: 0, y: 0, scale: 0, opacity: 0, rotate: 0 }}
          animate={{
            x: c.x, y: c.y, scale: [0, 1, 0.9, 0], opacity: [0, 1, 1, 0],
            rotate: c.spin,
          }}
          transition={{ duration: 0.85, delay: c.delay, ease: "easeOut" }}
        />
      ))}
      <motion.div
        className="relative flex flex-col items-center gap-1.5 text-success"
        initial={{ scale: reduced ? 1 : 0.4, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={reduced
          ? { duration: 0.15 }
          : { type: "spring", stiffness: 460, damping: 15 }}
      >
        <span className="grid place-items-center size-12 rounded-full bg-card shadow-float">
          <motion.span
            className="grid place-items-center text-success"
            initial={{ y: 0 }}
            animate={reduced ? { y: 0 } : { y: [0, -3, 0] }}
            transition={{ duration: 0.7, repeat: reduced ? 0 : Infinity, ease: "easeInOut" }}
          >
            <Rocket size={22} aria-hidden />
          </motion.span>
        </span>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-card/95 px-3 py-1
          text-[13px] font-bold shadow-card">
          <PartyPopper size={14} aria-hidden /> {label}
        </span>
      </motion.div>
    </div>
  );
}

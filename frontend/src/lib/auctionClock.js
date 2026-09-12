import { useCallback, useMemo, useSyncExternalStore } from "react";

/* The auction clock — how long a live auction has left, as a string that
 * keeps itself true.
 *
 * An auction is the one listing in the app with a DEADLINE. Everything else
 * on a card is a standing fact: a Buy It Now price is the price until the
 * seller changes it, a watcher count is the count until eBay reports another.
 * An auction is a thing that finishes, at a second nobody can move, and the
 * whole shape of what the seller should do about it -- share it, watch it,
 * leave it alone -- depends on whether that second is three days out or four
 * minutes. The grid already said an auction had a bid on it; it never said
 * the bidding was nearly over.
 *
 * Two halves, and the split is the point:
 *
 *   `auctionCountdown` is pure -- a deadline and a moment in, a string out.
 *   That is what the tests read, and what makes "what does this say with 61
 *   seconds left" a question with an answer rather than a screenshot.
 *
 *   `useAuctionCountdown` is the tick. ONE interval serves every clock on the
 *   page (a grid of twenty auctions is twenty subscribers, not twenty
 *   timers), and a subscriber only re-renders when the text it would print
 *   actually changes -- so a card reading "2d 4h" redraws twice a day, not
 *   172,800 times.
 *
 * The deadline itself comes from eBay, on the same sweep that carries the
 * bids (backend/services/ebay_trading.active_listing_counts), and it is an
 * instant rather than a duration for the same reason this module recomputes
 * from `Date.now()` instead of decrementing: the answer is cached on the
 * server for two minutes and then sits in a browser tab for an afternoon.
 * Anything counted down from "time left as of when eBay was asked" would be
 * wrong by however long that took, and would never find out.
 */

const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/* When a clock stops being a date and starts being a countdown.
 *
 * Under an hour is when an auction is actually happening -- it is where the
 * bidding lands, and it is the window in which anything the seller might do
 * about it still has time to work. So it is where the clock starts showing
 * seconds and the chip turns amber: the same threshold for both, so the
 * colour never has to be explained. Above it the seconds would be a moving
 * digit nobody reads on a grid of twenty cards. */
export const ENDING_SOON_MS = HOUR;

const pad = (n) => String(n).padStart(2, "0");

/** How long until `iso`, in the coarsest units that still say something.
 *
 *  Explicit units at every size ("9m 05s", never "9:05"): a bare clock face
 *  under an hour is read as hours and minutes by exactly the seller who has
 *  four minutes left to look at it.
 *
 *  Returns null for no deadline and for one that can't be read -- a card
 *  shows no clock at all rather than an invented one. A deadline already
 *  past is NOT null: an auction whose time is up is over, which is a fact
 *  about the listing worth saying while the app waits for eBay's next sweep
 *  to confirm the sale.
 */
export function auctionCountdown(iso, now = Date.now()) {
  const end = Date.parse(iso || "");
  if (!Number.isFinite(end)) return null;
  return countdownFrom(end - now, end);
}

function countdownFrom(left, end) {
  if (left <= 0) {
    return { left: 0, text: "Ended", ended: true, endingSoon: false, endsAt: end };
  }
  const days = Math.floor(left / DAY);
  const hours = Math.floor((left % DAY) / HOUR);
  const mins = Math.floor((left % HOUR) / MINUTE);
  const secs = Math.floor((left % MINUTE) / SECOND);
  const text = days > 0 ? `${days}d ${hours}h`
    : hours > 0 ? `${hours}h ${mins}m`
      : mins > 0 ? `${mins}m ${pad(secs)}s`
        : `${secs}s`;
  return { left, text, ended: false, endingSoon: left < ENDING_SOON_MS, endsAt: end };
}

/* The reading on the clock, rounded down to whatever the text is actually
 * printing -- whole seconds in the last hour, whole minutes before it.
 *
 * That rounding is what makes this a SNAPSHOT rather than a measurement. It
 * holds still for as long as the card says the same thing and moves the
 * instant a digit does, which is exactly the question `useSyncExternalStore`
 * asks a store every tick, and it means a grid of twenty auctions three days
 * out answers "no change" twenty times a second and renders nothing. Rounding
 * down (never to nearest) is what keeps "1s left" from being printed over an
 * auction that has already closed.
 *
 * null for a deadline that cannot be read; 0 for one that has passed.
 */
function displayLeft(iso, now = Date.now()) {
  const end = Date.parse(iso || "");
  if (!Number.isFinite(end)) return null;
  const left = end - now;
  if (left <= 0) return 0;
  const grain = left < ENDING_SOON_MS ? SECOND : MINUTE;
  return Math.floor(left / grain) * grain;
}

/** The deadline written out in full, for the tooltip the chip cannot fit.
 *  In the reader's own timezone: eBay reports UTC, and a seller in Denver
 *  being told their auction ends at 01:04 is being told the wrong day. */
export function auctionEndLabel(iso) {
  const end = Date.parse(iso || "");
  if (!Number.isFinite(end)) return "";
  try {
    return new Date(end).toLocaleString(undefined, {
      weekday: "short", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit",
    });
  } catch (e) {
    return new Date(end).toISOString();
  }
}

/* One tick for the whole page.
 *
 * Started by the first clock on screen and stopped by the last one leaving,
 * so a store with no live auctions in view runs no timer at all. */
const listeners = new Set();
let timer = null;

function fire() {
  // Copied: a listener that unsubscribes as it fires (a card unmounting on
  // the tick that ends its auction) must not disturb the walk.
  for (const listener of Array.from(listeners)) listener();
}

export function subscribeToTick(listener) {
  listeners.add(listener);
  if (timer == null) {
    timer = setInterval(fire, SECOND);
    // A hidden tab has its timers throttled to roughly one a minute, so a
    // clock the seller comes back to would be up to a minute stale until the
    // next one. Firing on the way back redraws every clock at once, which is
    // also what covers a laptop coming out of sleep.
    document.addEventListener("visibilitychange", fire);
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size && timer != null) {
      clearInterval(timer);
      timer = null;
      document.removeEventListener("visibilitychange", fire);
    }
  };
}

// A listing with no deadline subscribes to nothing and unsubscribes from it.
const NEVER = () => {};

/** The countdown for `iso`, kept current. Null when there is no readable
 *  deadline, which is what a card renders nothing for.
 *
 *  The tick is an external store and this reads it as one, which is what
 *  keeps the render pure: the clock is never copied into state that could go
 *  stale behind it (including on the render that hands the card a different
 *  listing), and React's own identity check on the snapshot decides when to
 *  redraw -- so the "has anything changed?" guard is not something this hook
 *  has to get right by hand.
 */
export function useAuctionCountdown(iso) {
  const subscribe = useCallback(
    (onTick) => (iso ? subscribeToTick(onTick) : NEVER), [iso]);
  const read = () => displayLeft(iso);
  const left = useSyncExternalStore(subscribe, read, read);
  const end = Date.parse(iso || "");
  return useMemo(
    () => (left == null ? null : countdownFrom(left, Number.isFinite(end) ? end : 0)),
    [left, end]);
}

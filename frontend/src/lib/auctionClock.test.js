/* What an auction clock says, at every size of "how long is left".
 *
 * The formatting is the whole feature: a countdown that reads wrong at a
 * glance is worse than no countdown, because the seller acts on it. So the
 * units are explicit at every size, the last hour is the one that counts
 * seconds, and a deadline already past says so rather than rolling over into
 * a negative number or quietly disappearing.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  auctionCountdown, auctionEndLabel, subscribeToTick,
} from "@/lib/auctionClock";

const NOW = Date.parse("2026-09-12T12:00:00.000Z");
const inMs = (ms) => new Date(NOW + ms).toISOString();
const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

const at = (ms) => auctionCountdown(inMs(ms), NOW);

describe("how long an auction has left", () => {
  it("counts days and hours while the auction is still a date", () => {
    expect(at(2 * DAY + 4 * HOUR).text).toBe("2d 4h");
    // The hours are the half that moves; a lone "2d" would sit still for a
    // whole day on a listing the seller is deciding whether to promote.
    expect(at(2 * DAY).text).toBe("2d 0h");
    expect(at(9 * DAY + 23 * HOUR + 59 * MINUTE).text).toBe("9d 23h");
  });

  it("drops to hours and minutes inside the last day", () => {
    expect(at(23 * HOUR + 59 * MINUTE).text).toBe("23h 59m");
    expect(at(4 * HOUR + 9 * MINUTE + 30 * SECOND).text).toBe("4h 9m");
    expect(at(HOUR).text).toBe("1h 0m");
  });

  it("counts seconds in the last hour, where the bidding happens", () => {
    // One millisecond under the hour is the first reading with seconds on it.
    expect(at(HOUR - 1).text).toBe("59m 59s");
    expect(at(9 * MINUTE + 5 * SECOND).text).toBe("9m 05s");
    expect(at(45 * SECOND).text).toBe("45s");
    expect(at(1).text).toBe("0s");
  });

  it("pads the seconds so the clock does not jump about as it counts", () => {
    // 9m 9s -> 9m 08s would be a chip that changes width every ten seconds,
    // in a row of cards the eye is trying to hold still.
    expect(at(9 * MINUTE + 9 * SECOND).text).toBe("9m 09s");
    expect(at(9 * MINUTE + 10 * SECOND).text).toBe("9m 10s");
  });

  it("names the last hour as ending soon, and nothing above it", () => {
    expect(at(HOUR - 1).endingSoon).toBe(true);
    expect(at(30 * MINUTE).endingSoon).toBe(true);
    // On the hour exactly it is not yet the final hour.
    expect(at(HOUR).endingSoon).toBe(false);
    expect(at(2 * DAY).endingSoon).toBe(false);
  });

  it("says a finished auction has ended rather than counting backwards", () => {
    for (const past of [0, -1, -5 * MINUTE, -3 * DAY]) {
      const done = at(past);
      expect(done.ended).toBe(true);
      expect(done.text).toBe("Ended");
      expect(done.left).toBe(0);
      // "Ended" is a statement, not an alarm: there is nothing left to catch
      // in time, and the next sweep of eBay will say whether it sold.
      expect(done.endingSoon).toBe(false);
    }
  });

  it("has no answer at all for a deadline it cannot read", () => {
    // Null, never a clock: the card draws nothing rather than inventing a
    // deadline — the same rule the bids and watchers beside it follow.
    expect(auctionCountdown("", NOW)).toBeNull();
    expect(auctionCountdown(null, NOW)).toBeNull();
    expect(auctionCountdown(undefined, NOW)).toBeNull();
    expect(auctionCountdown("not a date", NOW)).toBeNull();
  });

  it("reads eBay's own timestamp format", () => {
    // What GetMyeBaySelling actually puts in ListingDetails/EndTime.
    expect(auctionCountdown("2026-09-14T16:00:00.000Z", NOW).text)
      .toBe("2d 4h");
  });
});

describe("the deadline, written out for the tooltip", () => {
  it("spells out the day and time an auction ends", () => {
    const label = auctionEndLabel("2026-09-14T16:00:00.000Z");
    expect(label).toBeTruthy();
    expect(label).toContain("14");
  });

  it("says nothing at all where there is no readable deadline", () => {
    expect(auctionEndLabel("")).toBe("");
    expect(auctionEndLabel("not a date")).toBe("");
  });
});

describe("the tick every clock on the page shares", () => {
  afterEach(() => vi.useRealTimers());

  it("runs one timer for every clock, and none once they are gone", () => {
    vi.useFakeTimers();
    const a = vi.fn();
    const b = vi.fn();

    const dropA = subscribeToTick(a);
    const dropB = subscribeToTick(b);
    // One interval, not one per subscriber: a grid of twenty auctions must
    // not be twenty timers.
    expect(vi.getTimerCount()).toBe(1);

    vi.advanceTimersByTime(1000);
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);

    dropA();
    // Still one clock on screen, so the tick keeps running for it.
    expect(vi.getTimerCount()).toBe(1);
    vi.advanceTimersByTime(1000);
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(2);

    dropB();
    // And a store with nothing counting down runs no timer at all.
    expect(vi.getTimerCount()).toBe(0);
  });

  it("redraws on the way back to a tab that was hidden", () => {
    // A backgrounded tab has its timers throttled to about one a minute, so
    // without this the seller returns to a clock up to a minute stale — on
    // exactly the listing they came back to look at.
    vi.useFakeTimers();
    const seen = vi.fn();
    const drop = subscribeToTick(seen);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(seen).toHaveBeenCalledTimes(1);
    drop();
    // And it stops listening when the last clock goes.
    document.dispatchEvent(new Event("visibilitychange"));
    expect(seen).toHaveBeenCalledTimes(1);
  });
});

/* An auction on the grid says how long it has left, and keeps saying it.
 *
 * An auction is the one listing in the app with a deadline, and the card
 * never mentioned it: a listing with a bid on it looked the same three days
 * out as it did four minutes out. Those are not the same listing — the first
 * is worth sharing, the second is worth watching — and the seller had to open
 * eBay to tell them apart.
 *
 * The deadline rides in on the metrics overlay, from the same sweep that
 * already carried the bids (backend/services/ebay_trading), so the rules are
 * that overlay's rules: live listings only, and a card with no answer draws
 * no clock rather than inventing a deadline. eBay reports an end time for
 * auctions and nothing else, which is what keeps a Buy It Now from counting
 * down to its own renewal date.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ListingCard } from "@/components/ListingCard";
import { useAuctionCountdown } from "@/lib/auctionClock";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const NOW = Date.parse("2026-09-12T12:00:00.000Z");
const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

const endsIn = (ms) => new Date(NOW + ms).toISOString();

const AUCTION = {
  title: "Levi's 501 Vintage 1995 32x30",
  listing_format: "AUCTION",
  auction_start_price: 9.99,
  currency: "USD",
};

let root;
let host;

function render(props) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => {
    root.render(<ListingCard onOpen={() => {}} {...props} />);
  });
  return host;
}

function rerender(props) {
  act(() => {
    root.render(<ListingCard onOpen={() => {}} {...props} />);
  });
}

const live = (over = {}) => ({
  id: "s1", status: "live", listing: AUCTION, ...over,
});

const text = () => host.textContent;
const clock = () => host.querySelector('[title*="This auction"]');

// Let the shared tick run, one second at a time — as real time delivers it.
// Advancing several at once would land them in a single act() and React
// would batch them into one render, which is exactly what the redraw counts
// below are here to measure.
function tick(seconds = 1) {
  for (let i = 0; i < seconds; i += 1) {
    act(() => { vi.advanceTimersByTime(1000); });
  }
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  root = undefined;
  host = undefined;
  vi.useRealTimers();
});

describe("the auction clock on a card", () => {
  it("shows how long a live auction has left", () => {
    render({ item: live(), metrics: { bids: 1, ends_at: endsIn(2 * DAY + 4 * HOUR) } });
    expect(text()).toContain("2d 4h left");
  });

  it("counts itself down without the card being touched", () => {
    render({ item: live(), metrics: { bids: 1, ends_at: endsIn(3 * MINUTE) } });
    expect(text()).toContain("3m 00s left");
    tick(1);
    expect(text()).toContain("2m 59s left");
    tick(59);
    expect(text()).toContain("2m 00s left");
  });

  it("says so when the time runs out, instead of counting past it", () => {
    render({ item: live(), metrics: { bids: 4, ends_at: endsIn(2 * SECOND) } });
    expect(text()).toContain("2s left");
    tick(3);
    // The app finds out whether it sold on the next sweep of eBay. Until
    // then "Ended" is the true thing to say, and a clock that vanished would
    // leave the card looking like the auction never had one.
    expect(text()).toContain("Auction ended");
    expect(text()).not.toContain("left");
  });

  it("goes amber in the final hour and not before", () => {
    const amber = () => clock().className.includes("text-warning");
    render({ item: live(), metrics: { ends_at: endsIn(HOUR + SECOND) } });
    expect(amber()).toBe(false);
    rerender({ item: live(), metrics: { ends_at: endsIn(59 * MINUTE) } });
    // Same threshold that starts the seconds counting, so the colour never
    // needs explaining: it turns amber exactly when it starts ticking.
    expect(amber()).toBe(true);
    expect(text()).toContain("59m 00s left");
  });

  it("puts the exact end time in the tooltip the chip has no room for", () => {
    render({ item: live(), metrics: { ends_at: endsIn(2 * DAY) } });
    expect(clock().getAttribute("title")).toContain("This auction ends");
    expect(clock().getAttribute("title")).toContain("highest bidder");
  });

  it("draws no clock where eBay gave no deadline", () => {
    // A Buy It Now, whose end time is a renewal date nobody is racing — the
    // backend never sends one, and absent stays absent rather than becoming
    // a countdown to the epoch.
    render({
      item: live({ listing: { title: "A jacket", price: 24.99 } }),
      metrics: { views: 82, watchers: 10 },
    });
    expect(text()).toContain("82");
    expect(clock()).toBeNull();
  });

  it("draws no clock before eBay has been asked at all", () => {
    // No metrics yet is "we could not ask", never "no deadline" — the same
    // rule the bids and watchers on this card already follow.
    render({ item: live() });
    expect(clock()).toBeNull();
  });

  it("keeps the clock off anything that is not live", () => {
    // A draft has no deadline to count to, and an ended or sold auction's is
    // settled history. Neither should carry a live overlay's clock even if
    // one is handed to it.
    for (const status of ["draft", "unlisted", "ended", "sold"]) {
      render({ item: live({ status }), metrics: { ends_at: endsIn(HOUR) } });
      expect(clock()).toBeNull();
      act(() => root.unmount());
      host.remove();
    }
    render({ item: live(), metrics: { ends_at: endsIn(HOUR) } });
    expect(clock()).not.toBeNull();
  });

  it("shows the clock in list layout too", () => {
    render({ item: live(), layout: "list",
             metrics: { views: 82, watchers: 10, ends_at: endsIn(5 * HOUR) } });
    expect(text()).toContain("5h 0m left");
  });

  it("follows the deadline when the card is handed a different one", () => {
    // The clock is read fresh at every render rather than copied into state,
    // so a recycled card can never spend a tick showing the last listing's
    // deadline.
    render({ item: live(), metrics: { ends_at: endsIn(2 * DAY) } });
    expect(text()).toContain("2d 0h left");
    rerender({ item: live({ id: "s2" }), metrics: { ends_at: endsIn(5 * MINUTE) } });
    expect(text()).toContain("5m 00s left");
  });

  it("only redraws when a digit actually moves", () => {
    // The reason the tick reports a rounded reading rather than the clock:
    // a grid of twenty auctions three days out is twenty components asked
    // "changed?" twenty times a second, and the answer has to be able to be
    // no. Without it every card in the store re-renders 86,400 times a day
    // to print the same two numbers.
    const draws = [];
    function Probe({ endsAt }) {
      const left = useAuctionCountdown(endsAt);
      draws.push(left.text);
      return <span>{left.text}</span>;
    }
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    // Three seconds short of the hour turning over, so the ticks either side
    // of it are the same reading and the turn itself is one second away.
    act(() => { root.render(<Probe endsAt={endsIn(2 * DAY + 4 * HOUR + 3 * SECOND)} />); });
    expect(draws).toEqual(["2d 4h"]);

    // Three ticks with nothing to say, and nothing drawn for them.
    tick(3);
    expect(draws).toEqual(["2d 4h"]);

    // The one that moves the hour draws — once.
    tick(1);
    expect(draws).toEqual(["2d 4h", "2d 3h"]);
  });

  it("does count every second once the auction is in its last hour", () => {
    const draws = [];
    function Probe({ endsAt }) {
      const left = useAuctionCountdown(endsAt);
      draws.push(left.text);
      return <span>{left.text}</span>;
    }
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    act(() => { root.render(<Probe endsAt={endsIn(10 * SECOND)} />); });
    tick(3);
    expect(draws).toEqual(["10s", "9s", "8s", "7s"]);
  });

  it("stops the shared tick when the last clock leaves the screen", () => {
    render({ item: live(), metrics: { ends_at: endsIn(HOUR) } });
    expect(vi.getTimerCount()).toBe(1);
    act(() => root.unmount());
    // A store with nothing counting down runs no timer: the interval belongs
    // to the clocks on screen, not to the app.
    expect(vi.getTimerCount()).toBe(0);
    host.remove();
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
  });
});

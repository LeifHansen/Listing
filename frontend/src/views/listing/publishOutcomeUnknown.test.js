/**
 * The other half of "we can't tell what happened" — the half the SERVER owns.
 *
 * publishTimeout.test.js covers the case where this client loses the answer.
 * This is the case where the request reached eBay and the answer was lost on
 * the way back to the server. The app is not guessing there either: the
 * Trading client raises its own UnknownOutcome, and backend/ebay_errors turns
 * it into an issue whose own comment explains the stakes —
 *
 *     "A seller who reads 'rejected' fixes something and publishes again,
 *      which is how the duplicate live listing happens."
 *
 * — and then the publish response carried no machine-readable flag, so the
 * two screens that COUNT publishes rather than render them had nothing to
 * count on. Both filed it under `failed` and wrote:
 *
 *     "Published 5 listings. All 2 were refused: We could not confirm what
 *      eBay did"
 *
 * A sentence that contradicts itself, directly above the one action that must
 * not be taken. The providers now stamp `outcome_unknown` on the response,
 * and publishTally is the single place both queues ask what an attempt was.
 */
import { describe, expect, it } from "vitest";

import {
  blockedReason, outcomeUnknown, publishTally, UNCONFIRMED_PUBLISH,
} from "./publishShared";

const FALLBACK = "Publish blocked — open the draft to see what to fix.";

// What the server sends when it could not establish what eBay did: the legacy
// single-eBay body, which is the shape both bulk queues publish through.
const LOST = {
  published: false,
  outcome_unknown: true,
  message: "The request reached eBay and the answer didn't come back.",
  issues: [{
    target: "generic", level: "error",
    title: "We could not confirm what eBay did",
    fix: "Check this item in your eBay listings before trying again — "
       + "retrying blind could publish it twice.",
  }],
};

// A real rejection: a field to fix, and the seller's move.
const REFUSED = {
  published: false,
  message: "Add a package weight.",
  issues: [{ target: "shipping", level: "error",
             title: "eBay needs a package weight", fix: "Add one." }],
};

const LIVE = { published: true, listing_id: "1122334455" };

describe("outcomeUnknown", () => {
  it("reads the flag off a single-eBay publish", () => {
    expect(outcomeUnknown(LOST)).toBe(true);
  });

  it("reads it off any marketplace in a fan-out", () => {
    expect(outcomeUnknown({
      multi: true, published: false,
      results: { ebay: { ok: false, outcome_unknown: true }, etsy: { ok: false } },
    })).toBe(true);
  });

  it("is false for a refusal — the absence is the answer", () => {
    expect(outcomeUnknown(REFUSED)).toBe(false);
    expect(outcomeUnknown({
      multi: true, results: { ebay: { ok: false }, etsy: { ok: true } },
    })).toBe(false);
  });

  it("is false for a publish that went through, and for no response at all", () => {
    expect(outcomeUnknown(LIVE)).toBe(false);
    expect(outcomeUnknown(undefined)).toBe(false);
    expect(outcomeUnknown(null)).toBe(false);
  });
});

describe("publishTally", () => {
  it("counts an unanswered publish apart from the refusals", () => {
    const tally = publishTally(LOST, FALLBACK);

    expect(tally.published).toBe(false);
    expect(tally.unconfirmed).toBe(true);
  });

  it("tells that seller to check, not to fix", () => {
    const tally = publishTally(LOST, FALLBACK);

    expect(tally.reason).toBe(UNCONFIRMED_PUBLISH);
    expect(tally.reason).toMatch(/check your store/i);
    // The defect, stated: routed through blockedReason this reads as eBay's
    // verdict on the listing, and the summary line then calls it a refusal.
    expect(tally.reason).not.toBe(blockedReason(LOST, FALLBACK));
  });

  it("still calls a refusal a refusal, in eBay's own resolved words", () => {
    const tally = publishTally(REFUSED, FALLBACK);

    expect(tally.unconfirmed).toBe(false);
    expect(tally.reason).toBe("eBay needs a package weight");
  });

  it("falls back when a refusal named nothing", () => {
    expect(publishTally({ published: false }, FALLBACK).reason).toBe(FALLBACK);
  });

  it("has nothing to say about a listing that went live", () => {
    const tally = publishTally(LIVE, FALLBACK);

    expect(tally.published).toBe(true);
    expect(tally.unconfirmed).toBe(false);
    expect(tally.reason).toBe(null);
  });

  it("lets a fan-out that put something live count as published", () => {
    // Deliberate ordering: something IS live, so the item leaves Drafts and
    // the batch counts it. What could not be confirmed rides along in the
    // per-marketplace results the card and the editor render from.
    const tally = publishTally({
      multi: true, published: true,
      results: { ebay: { ok: false, outcome_unknown: true },
                 etsy: { ok: true, published: true } },
    }, FALLBACK);

    expect(tally.published).toBe(true);
  });
});

/* What a seller is told when a publish does not go live.
 *
 * The defect these pin down: every publish surface led with res.message, which
 * is eBay's own sentence. For eBay's catch-all error 240 that sentence is
 * "The item cannot be listed or modified. The title and/or description may
 * contain improper words or the listing or seller may be in violation of eBay
 * policy" -- an ACCOUNT-level hold whose text blames the title first. It
 * repeats on every listing, so a seller reads it once and starts rewriting
 * titles that were never the problem.
 *
 * The backend already knows better: it files a 240 under target "account" and
 * asks eBay whether payments onboarding is the hold. That answer arrives in
 * res.issues and was being thrown away by the one piece of UI a seller cannot
 * miss.
 */
import { describe, expect, it } from "vitest";
import { blockedReason, fixTargetFor } from "./publishShared";

const E240 = "The item cannot be listed or modified. The title and/or "
  + "description may contain improper words or the listing or seller may be "
  + "in violation of eBay policy.";

describe("blockedReason", () => {
  it("leads with the account hold, not eBay's sentence about titles", () => {
    const res = {
      published: false,
      message: E240,
      issues: [{
        target: "account",
        level: "error",
        title: "This eBay account hasn't finished payments setup",
        fix: "Finish it on eBay under Seller Hub → Payments.",
      }],
    };
    expect(blockedReason(res)).toBe(
      "This eBay account hasn't finished payments setup");
    expect(blockedReason(res)).not.toContain("improper words");
  });

  it("prefers a targeted issue over a generic one", () => {
    const res = {
      message: E240,
      issues: [
        { target: "generic", level: "error", title: "Something went wrong" },
        { target: "account", level: "error", title: "This account is on hold" },
      ],
    };
    expect(blockedReason(res)).toBe("This account is on hold");
  });

  it("ignores warnings — they are not why the publish stopped", () => {
    const res = {
      message: "The price is invalid.",
      issues: [{ target: "photos", level: "warn", title: "Only one photo" }],
    };
    expect(blockedReason(res)).toBe("The price is invalid.");
  });

  it("reads issues out of a multi-marketplace fan-out", () => {
    const res = {
      multi: true,
      message: E240,
      results: {
        ebay: { ok: false, issues: [{ target: "account", level: "error",
          title: "This eBay account hasn't finished payments setup" }] },
        etsy: { ok: true, issues: [] },
      },
    };
    expect(blockedReason(res)).toBe(
      "This eBay account hasn't finished payments setup");
  });

  it("falls back to eBay's words when the app has nothing better", () => {
    expect(blockedReason({ message: "The price is invalid." }))
      .toBe("The price is invalid.");
  });

  it("falls back to the caller's text when there is no message at all", () => {
    expect(blockedReason({}, "Publish blocked.")).toBe("Publish blocked.");
    expect(blockedReason(undefined, "Publish blocked.")).toBe("Publish blocked.");
  });
});

/* A 240 eBay would not explain arrives flagged `placeholder`: "the publish
 * stopped, nobody said why". It is an account-target issue like any other, so
 * it used to win the pick outright -- and every bulk card, drafts-strip row
 * and publish toast showed it while the diagnosis that named the real hold sat
 * underneath, unread. Seven failed drafts, seven identical dead ends. */
describe("blockedReason with an unexplained 240", () => {
  const placeholder = {
    target: "account", level: "error", placeholder: true,
    title: "eBay refused this listing and wouldn't say why",
  };

  it("shows the diagnosis, not the placeholder it arrived with", () => {
    const res = {
      message: E240,
      issues: [
        { target: "title", level: "error",
          title: "eBay is refusing this listing's title" },
        placeholder,
      ],
    };
    expect(blockedReason(res)).toBe("eBay is refusing this listing's title");
  });

  it("still shows the placeholder when it is all there is", () => {
    expect(blockedReason({ message: E240, issues: [placeholder] }))
      .toBe("eBay refused this listing and wouldn't say why");
  });

  it("prefers a named cause even when the placeholder comes first", () => {
    const res = {
      issues: [placeholder,
        { target: "account", level: "error",
          title: "This eBay account hasn't finished payments setup" }],
    };
    expect(blockedReason(res)).toBe(
      "This eBay account hasn't finished payments setup");
  });
});

describe("fixTargetFor", () => {
  it("jumps to the field a diagnosis names", () => {
    expect(fixTargetFor([
      { target: "account", level: "error", placeholder: true, title: "?" },
      { target: "title", level: "error", title: "eBay refuses the title" },
    ])).toBe("title");
  });

  it("has nowhere to jump for an account-wide problem", () => {
    expect(fixTargetFor([
      { target: "account", level: "error", placeholder: true, title: "?" },
    ])).toBe(null);
  });

  it("ignores warnings", () => {
    expect(fixTargetFor([{ target: "photos", level: "warn", title: "One photo" }]))
      .toBe(null);
  });
});

/* The reported listing is the first test, and the one that matters.
 *
 * "Miniature Subminiature Spy Camera Made in Japan with Box" — 56 characters,
 * accurate, and refused by eBay three times with nothing said beyond "the
 * title". The scan has to name "Spy" out of that sentence, and it has to stay
 * quiet on the rest of it, or it is the same dead end with more words.
 */
import { describe, expect, it } from "vitest";
import { riskyWords, riskyWordSummary, RISKY_TERMS } from "./riskyWords";

const REPORTED = "Miniature Subminiature Spy Camera Made in Japan with Box";

describe("the listing eBay refused", () => {
  it("names the word", () => {
    const hits = riskyWords(REPORTED);
    expect(hits.map((h) => h.id)).toEqual(["spy"]);
    // The seller's own spelling, and the longest phrase of it that is in the
    // title: "Spy Camera" is what they read in the box and what they have to
    // change, where a bare "Spy" would send them looking for it.
    expect(hits[0].match).toBe("Spy Camera");
    expect(hits[0].severity).toBe("high");
    expect(hits[0].suggest).toMatch(/subminiature/i);
  });

  it("says it in one line for the refusal", () => {
    expect(riskyWordSummary(REPORTED)).toBe(
      "“Spy Camera” is a word eBay's filters are known to refuse.");
  });

  it("leaves the title alone once the word is gone", () => {
    expect(riskyWords("Miniature Subminiature Camera Made in Japan with Box"))
      .toEqual([]);
  });
});

describe("a word inside a word is not the word", () => {
  // The whole difference between a useful flag and a nuisance — and the very
  // mistake eBay's own filter gets accused of. Each of these CONTAINS a term
  // and is not one.
  it.each([
    "Raspy Voice Blues LP Record",        // spy
    "Vintage Outfits Bundle Size 10",     // fits
    "Espionage Novel First Edition",      // spy
    "Clonezilla Boot Disk",               // clone
    "Crispy Rice Cereal Box 1985",        // spy
  ])("stays quiet on %s", (title) => {
    expect(riskyWords(title)).toEqual([]);
  });

  it("still fires on the words themselves", () => {
    expect(riskyWords("Spy pen").map((h) => h.id)).toEqual(["spy"]);
    expect(riskyWords("Fits Nike Air").map((h) => h.id)).toEqual(["comparison"]);
    expect(riskyWords("Clone stamp tool").map((h) => h.id)).toEqual(["fake"]);
  });
});

describe("what it flags", () => {
  it.each([
    ["Rolex Replica Watch Gold", "replica", "high"],
    ["Payment by money order only", "off-ebay-payment", "high"],
    ["Email me for more photos", "off-platform-contact", "high"],
    ["Hidden camera clock radio", "hidden-camera", "high"],
    ["Antique Ivory Handle Brush", "restricted-goods", "high"],
    ["100% Authentic Louis Vuitton Bag", "authenticity-claim", "caution"],
    ["Nintendo Game Boy MINT CONDITION", "condition-claim", "caution"],
    ["Handling includes insurance", "insurance", "caution"],
    ["L@@K rare vintage lamp", "keyword-spam", "caution"],
  ])("flags %s as %s", (text, id, severity) => {
    const hits = riskyWords(text);
    expect(hits.map((h) => h.id)).toContain(id);
    expect(hits.find((h) => h.id === id).severity).toBe(severity);
  });

  it("puts what eBay refuses ahead of what it merely dislikes", () => {
    const hits = riskyWords("Authentic spy camera");
    expect(hits.map((h) => h.severity)).toEqual(["high", "caution"]);
  });

  it("reports a term once, at its longest match", () => {
    // "spy camera" and a bare "spy" are one problem with this title.
    const hits = riskyWords("Spy camera and spy lens");
    expect(hits).toHaveLength(1);
    expect(hits[0].match.toLowerCase()).toBe("spy camera");
  });

  it("has nothing to say about an ordinary listing", () => {
    expect(riskyWords("Nike Air Max 90 Men's 10.5 White Leather Sneakers"))
      .toEqual([]);
    expect(riskyWordSummary("Nike Air Max 90 Men's 10.5")).toBe("");
  });

  it.each([null, undefined, "", "   "])("survives %s", (text) => {
    expect(riskyWords(text)).toEqual([]);
    expect(riskyWordSummary(text)).toBe("");
  });
});

describe("the list itself", () => {
  it("explains every term it flags", () => {
    // A flag without a reason is eBay's error message with a different logo
    // on it. Every entry has to say why, and what to write instead.
    for (const term of RISKY_TERMS) {
      expect(term.words.length, term.id).toBeGreaterThan(0);
      expect(term.why, term.id).toBeTruthy();
      expect(term.suggest, term.id).toBeTruthy();
      expect(["high", "caution"], term.id).toContain(term.severity);
    }
  });

  it("uses ids that are unique", () => {
    const ids = RISKY_TERMS.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

/* Words eBay's listing filters refuse, checked while the seller types.
 *
 * The case this was written for: a vintage camera listed as "Miniature
 * Subminiature Spy Camera Made in Japan with Box". eBay refused it, blamed
 * the title, and said nothing else. The title was 56 characters, correctly
 * spelled and accurate, so there was nothing to act on — the seller rewrote
 * it, republished, was refused again, started the listing over from scratch,
 * and was refused again. The word "spy" was the whole of it.
 *
 * eBay does not publish the list. It refuses with error 240 — "the title
 * and/or description may contain improper words, or the listing or seller may
 * be in violation of eBay policy" — which names four possible causes and none
 * of the actual one. So this is assembled from eBay's own published POLICIES
 * (what the filters are enforcing) plus the trigger words listing tools have
 * documented from the errors their sellers hit:
 *
 *   Electronic equipment policy — cameras disguised as other objects, and
 *     anything implying a device records people without their knowledge.
 *     https://www.ebay.com/help/policies/prohibited-restricted-items/electronic-equipment-policy?id=4302
 *   Counterfeit policy — replicas, fakes and anything built to pass as a
 *     brand's own.
 *     https://www.ebay.com/help/policies/prohibited-restricted-items/counterfeit-item-policy?id=4276
 *   Search manipulation policy — words that aren't about the item in hand.
 *     https://www.ebay.com/help/policies/listing-policies/search-browse-manipulation-policy?id=4243
 *   The error-240 trigger words listing tools publish (money order, cheque,
 *     mint condition, like new, insurance, mailto, iframe…), which is the
 *     nearest thing to eBay's filter list that exists in public.
 *
 * Two rules follow from eBay keeping the list secret, and both matter:
 *
 *   1. NOTHING HERE BLOCKS A PUBLISH. eBay's filters have false positives and
 *      so does this — "spy camera" is what a collector calls a subminiature
 *      camera, and eBay does sell them. A seller who knows their word is fine
 *      publishes anyway. See ebayBlockers in views/listing/blockers.js: this
 *      list is deliberately not in it.
 *   2. Every entry says WHY, so the seller can judge it. "Risky word" on its
 *      own is the same dead end as eBay's own error.
 *
 * Used in two places, from this one list: the editor flags words as they are
 * typed (TitleCard, DescriptionCard), and when eBay refuses a listing over
 * its title the same scan names the word in the refusal instead of leaving
 * the seller to guess which of eight it was.
 */

// severity:
//   "high"    — eBay policy prohibits this outright, or the filter is known
//               to refuse the listing over the word. Expect a refusal.
//   "caution" — a word that trips filters in some categories and is ordinary
//               in others. Worth a look, not worth a panic.
export const RISKY_TERMS = [
  // --- surveillance and covert recording --------------------------------
  // The one that cost a seller three publishes and a rebuilt listing. eBay's
  // electronic equipment policy bans cameras disguised as other objects and
  // any listing implying a device can record people unaware; the filter reads
  // the word, not the item, so a 1950s collectible is refused the same as a
  // pen camera.
  {
    id: "spy", words: ["spy", "spycam", "spy cam", "spy camera"],
    severity: "high", label: "spy",
    why: "eBay's electronic equipment policy bans covert recording devices, "
       + "and the filter reads the word rather than the item — collectible "
       + "subminiature cameras get refused for it too.",
    suggest: "Call it what the collectors' market calls it: “subminiature”, "
           + "“miniature”, “compact” or the model name.",
  },
  {
    id: "hidden-camera", words: ["hidden camera", "hidden cam", "covert",
                                 "nanny cam", "disguised camera",
                                 "secret camera", "undetectable"],
    severity: "high", label: "hidden camera",
    why: "Cameras built into or disguised as other objects are prohibited "
       + "outright, and wording that suggests one is refused with them.",
    suggest: "Describe the item plainly — its form, size and model.",
  },
  {
    id: "wiretap", words: ["wiretap", "bugging device", "listening device",
                           "signal jammer", "jammer"],
    severity: "high", label: "interception equipment",
    why: "Equipment for intercepting signals or conversations is prohibited "
       + "on eBay and restricted by the FCC.",
    suggest: "Remove it. If the item genuinely is one of these, eBay won't "
           + "list it at all.",
  },

  // --- payment off eBay ---------------------------------------------------
  // The classic 240. eBay's filter reads any payment method as an offer to
  // settle off-platform, and it does not care that the sentence says the
  // opposite: "we do not accept money orders" is refused just as fast.
  {
    id: "off-ebay-payment",
    words: ["money order", "cashier's check", "cashiers check", "western union",
            "paypal", "venmo", "cash app", "cashapp", "zelle", "wire transfer",
            "bank transfer", "e-check", "moneygram"],
    severity: "high", label: "payment method",
    why: "eBay reads any payment method in a listing as an offer to deal "
       + "outside eBay — even a sentence saying you DON'T accept it.",
    suggest: "Take it out. eBay handles payment; the listing doesn't need to "
           + "mention it.",
  },

  // --- contact and markup -------------------------------------------------
  {
    id: "off-platform-contact",
    words: ["email me", "e-mail me", "call me", "text me", "phone me",
            "contact me at", "mailto", "whatsapp", "my website", "our website",
            "off ebay", "outside ebay"],
    severity: "high", label: "contact details",
    why: "Moving a buyer off eBay is against its policies, and the filter "
       + "catches the invitation as well as the address.",
    suggest: "Remove it — buyers can reach you through eBay Messages.",
  },
  {
    id: "markup", words: ["iframe", "<script", "javascript:", "onclick"],
    severity: "high", label: "markup",
    why: "eBay strips and refuses active markup in listings.",
    suggest: "Remove it — plain text and eBay's own formatting only.",
  },

  // --- counterfeit and replica -------------------------------------------
  {
    id: "replica",
    words: ["replica", "counterfeit", "knockoff", "knock-off", "knock off",
            "bootleg", "unauthorized copy", "inspired by", "dupe"],
    severity: "high", label: "replica",
    why: "eBay's counterfeit policy bans unauthorised replicas, and the words "
       + "themselves are enough for the filter — whether or not the item is one.",
    suggest: "If the item is genuine, say so by naming the maker and model. "
           + "If it's unbranded, say “unbranded”.",
  },
  {
    id: "fake", words: ["fake", "clone", "faux leather"],
    severity: "caution", label: "fake",
    why: "Ordinary in some listings (fake fur, fake plants) and a counterfeit "
       + "flag in others. eBay's filter doesn't always tell them apart.",
    suggest: "“Faux”, “imitation” or “synthetic” describes the material "
           + "without tripping the counterfeit filter.",
  },
  {
    id: "reproduction", words: ["reproduction", "repro"],
    severity: "caution", label: "reproduction",
    why: "A documented error-240 trigger. Legitimate for parts and prints, "
       + "and read as a counterfeit signal elsewhere.",
    suggest: "Keep it if the item really is a reproduction — but expect eBay "
           + "to look twice, and don't pair it with a brand name.",
  },

  // --- claims about authenticity and condition ----------------------------
  // eBay's counterfeit and VeRO filters treat an authenticity claim on an
  // item nobody has authenticated as a red flag, and sellers report titles
  // removed over the word alone.
  {
    id: "authenticity-claim",
    words: ["authentic", "genuine", "100% authentic", "guaranteed authentic",
            "certified authentic", "certificate of authenticity"],
    severity: "caution", label: "authenticity claim",
    why: "eBay's counterfeit filters treat an authenticity claim as a flag "
       + "unless the item went through eBay Authenticity Guarantee. Listings "
       + "have been pulled over the word by itself.",
    suggest: "Drop the claim and let the brand name, model and photos do it. "
           + "Provenance belongs in the description, not the title.",
  },
  {
    id: "condition-claim", words: ["mint condition", "like new", "as new"],
    severity: "caution", label: "condition claim",
    why: "A documented error-240 trigger — eBay wants condition set in the "
       + "condition field, not claimed in words.",
    suggest: "Set the Condition field instead; eBay suggests “slightly used” "
           + "where you'd have written “like new”.",
  },
  {
    id: "insurance", words: ["insurance", "insured", "guarantee", "warranty"],
    severity: "caution", label: "guarantee",
    why: "eBay reads a promise it would have to stand behind. “Handling "
       + "includes insurance” is a documented 240.",
    suggest: "Leave shipping and cover to eBay's own terms; say what the item "
           + "is instead.",
  },

  // --- search manipulation ------------------------------------------------
  {
    id: "keyword-spam",
    words: ["l@@k", "must see", "wow", "look!!", "best price", "cheapest",
            "free shipping", "no reserve!!"],
    severity: "caution", label: "keyword spam",
    why: "eBay's search manipulation policy requires every word to describe "
       + "the item itself. Attention words describe the listing.",
    suggest: "Spend the characters on what buyers search for — brand, model, "
           + "size, colour, material.",
  },
  {
    id: "comparison", words: ["compatible with", "fits", "similar to",
                              "style of", "not authentic", "vs "],
    severity: "caution", label: "comparison",
    why: "Naming another product or brand to be found by it is search "
       + "manipulation — eBay is explicit about “fits”, “for” and “compatible "
       + "with” before a brand name.",
    suggest: "Name only what you're selling. Fitment belongs in the item "
           + "specifics, where eBay expects it.",
  },

  // --- restricted goods ---------------------------------------------------
  // Not "risky wording" — items eBay won't take. Flagged because the refusal
  // for these is the same unexplained 240, and a seller shouldn't have to
  // discover the category is closed by publishing into it.
  {
    id: "restricted-goods",
    words: ["ivory", "prescription", "narcotic", "stun gun", "taser",
            "silencer", "suppressor", "switchblade", "brass knuckles",
            "human remains", "assault rifle", "ammunition"],
    severity: "high", label: "restricted item",
    why: "eBay prohibits or restricts this category outright — the listing is "
       + "refused whatever the title says.",
    suggest: "Check eBay's prohibited and restricted items policy before "
           + "spending more time on this listing.",
  },
];

// A word inside a longer word is not the word: "spy" must fire on "Spy Camera"
// and stay silent on "raspy", and "fits" must not fire on "outfits" — which
// is the mistake eBay's own filter is accused of making, and the reason a
// tool that copies it would be worse than useless.
//
// Boundaries are built from the term rather than \b so that terms ending in a
// symbol still match: \b after the "@" in "l@@k" never fires.
function matcher(word) {
  const esc = word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const before = /^[a-z0-9]/i.test(word) ? "(?<![a-z0-9])" : "";
  const after = /[a-z0-9]$/i.test(word) ? "(?![a-z0-9])" : "";
  return new RegExp(`${before}${esc}${after}`, "i");
}

// Longest first, so "spy camera" is reported once as itself rather than twice
// as "spy" and "spy camera".
const COMPILED = RISKY_TERMS.flatMap((term) =>
  term.words.map((word) => ({ term, word, re: matcher(word) })))
  .sort((a, b) => b.word.length - a.word.length);

/* Every risky term in `text`, worst first.
 *
 * Returns [{ id, label, severity, match, why, suggest }] — `match` is the
 * seller's own spelling, because a flag that quotes the text back is one they
 * can find in the box.
 *
 * One hit per term: a title saying "spy camera, spy lens" has one thing wrong
 * with it, not two. */
export function riskyWords(text) {
  const subject = String(text || "");
  if (!subject.trim()) return [];
  const found = new Map();
  // Words already claimed by a longer match, so "spy camera" doesn't also
  // report a bare "spy" sitting inside it.
  const claimed = [];
  for (const { term, re } of COMPILED) {
    if (found.has(term.id)) continue;
    const hit = re.exec(subject);
    if (!hit) continue;
    const span = [hit.index, hit.index + hit[0].length];
    if (claimed.some(([s, e]) => span[0] >= s && span[1] <= e)) continue;
    claimed.push(span);
    found.set(term.id, {
      id: term.id, label: term.label, severity: term.severity,
      match: hit[0], why: term.why, suggest: term.suggest,
    });
  }
  return [...found.values()].sort(
    (a, b) => (a.severity === b.severity ? 0 : a.severity === "high" ? -1 : 1));
}

/* The one-line version, for a refusal that already named the title.
 *
 * "" when nothing is flagged — eBay's filter is wider than this list, and
 * inventing a culprit is worse than saying nothing. */
export function riskyWordSummary(text) {
  const hits = riskyWords(text);
  if (!hits.length) return "";
  const words = hits.map((h) => `“${h.match}”`);
  const list = words.length === 1 ? words[0]
    : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
  return `${list} ${words.length === 1 ? "is a word" : "are words"} eBay's `
       + "filters are known to refuse.";
}

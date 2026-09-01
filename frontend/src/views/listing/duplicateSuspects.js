/* Which two drafts in one bulk batch might be the SAME item drafted twice.

   A photo dump gets grouped into items by the server, and the grouping can
   split one item in two: eight photos of a jacket become a five-photo draft
   and a three-photo draft, each identified on its own. Publishing both puts
   two listings of one jacket on eBay. Catching that before publish is the
   whole point of the hint this feeds.

   The first version of it compared title words, and that is the wrong unit on
   its own. A pile of clothing is mostly the SAME words: a Lacoste polo and a
   Brooks Brothers polo share "fit", "polo", "shirt" and "men", which was
   enough to clear the old bar — four shared words out of seven — while
   "lacoste" and "brooks brothers 1818", the words that say these are two
   different shirts, counted for nothing. Sellers saw it on every batch of
   like-for-like inventory, which is most batches.

   So the question asked here is not "do these titles overlap" but "does
   anything say these are DIFFERENT items". The AI fills in brand, colour and
   size per draft; two drafts of one jacket agree on them, because they are
   the same jacket photographed twice. Any of them disagreeing settles it, and
   no amount of shared category words overrides that. Only once nothing
   contradicts does word overlap get a say, and then on words that survive
   having the category, the cut and the condition stripped out.

   Both gates fail SILENT — an unstated brand doesn't convict, a thin overlap
   doesn't either. A missed pair costs a seller one merge they do by hand; a
   false one is a warning about a mistake they did not make, on a screen that
   is asking them to trust its judgement about their inventory. */
import { specificValue } from "./specifics";

/* Words that describe a category, a cut, a condition or who an item is for —
   the parts of a title that two DIFFERENT things in one pile share. Dropped
   before any comparison, so agreement has to be about the item itself.

   Stemmed forms, because the stemmer runs first: "mens" arrives here as
   "men". The original list held "mens" and "womens", which therefore never
   matched anything and let gender words count as evidence of a duplicate. */
const STOP_WORDS = new Set([
  "the", "and", "with", "for", "from", "size", "men", "women", "unisex",
  "kid", "boy", "girl", "adult", "new", "nwt", "nwot", "used", "pre", "owned",
  "preowned", "vintage", "retro", "condition", "excellent", "great", "good",
  "authentic", "genuine", "official", "rare", "lot", "set", "item", "piece",
  "style", "fit", "slim", "regular", "relaxed", "classic", "casual",
]);

/* Colours, so a title can answer "what colour is it" when the AI didn't fill
   the specific in. A closed, short vocabulary on purpose: it exists to catch
   the blue-one-and-the-white-one case, not to name every shade. */
const COLORS = new Set([
  "black", "white", "grey", "gray", "silver", "gold", "beige", "cream",
  "ivory", "tan", "brown", "chocolate", "red", "burgundy", "maroon", "pink",
  "orange", "yellow", "green", "olive", "teal", "turquoise", "blue", "navy",
  "purple", "lavender", "khaki", "charcoal", "multicolor", "multicolour",
]);

// Written sizes and their letters are the same size. Without this, one draft
// saying "Large" and its twin saying "L" would read as two different items.
const SIZE_WORDS = {
  small: "s", medium: "m", med: "m", large: "l",
  xsmall: "xs", xlarge: "xl", xxlarge: "xxl", xxsmall: "xxs",
};

// The listing body, whichever shape the draft arrived in — bulk queue items
// carry {session_id, listing:{...}}, the merge dialog passes listings direct.
const bodyOf = (draft) => (draft && draft.listing) || draft || {};

const titleOf = (draft) =>
  String(bodyOf(draft).title || (draft && draft.title) || "");

/* A title's meaningful words, in the order they were written.

   Order matters for one thing only: the first word is where a brand goes when
   nothing else names it. Everything else reads this as a set. */
function titleWords(title) {
  return String(title || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ")
    .split(/\s+/)
    .map((w) => w.replace(/s$/, ""))  // light stemming: phases ≈ phase
    // Two letters is noise; two DIGITS is a model number. Dropping everything
    // short threw away the 90 in "Air Max 90" and the 11 in "Instax Mini 11",
    // which are the words that tell that shoe from the next one on the shelf.
    .filter((w) => (w.length > 2 || (w.length === 2 && /\d/.test(w)))
                   && !STOP_WORDS.has(w));
}

// An attribute's value as comparable words: "Navy Blue" and "Blue" overlap,
// "Large" and "L" are one word, "Brooks Brothers" and "Lacoste" share none.
function valueWords(value) {
  return new Set(String(value || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => SIZE_WORDS[w] || w));
}

const overlaps = (a, b) => [...a].some((w) => b.has(w));
const numbersIn = (words) => new Set([...words].filter((w) => /\d/.test(w)));
// Does a hold anything b never mentions?
const exclusive = (a, b) => [...a].some((w) => !b.has(w));

/* Do two answers to the same question disagree?

   Sharing a word is agreement: "Navy" and "Navy Blue" are one colour, "L" and
   "Large" one size, and two identify passes over one item word things
   differently all the time.

   Except across a number. A variant's number IS its identity — Air Max 90 and
   Air Max 95 have every other word in common, as do an iPhone 12 and an
   iPhone 13 — so a number each side names and the other never does settles
   it, and the words around it stop counting.

   Both sides have to have one. One draft saying "128GB" where its twin just
   didn't mention the capacity is a quieter description of one phone, not a
   second phone. */
function conflicts(a, b) {
  if (!a.size || !b.size) return false;  // silence is not a disagreement
  const na = numbersIn(a);
  const nb = numbersIn(b);
  if (exclusive(na, nb) && exclusive(nb, na)) return true;
  return !overlaps(a, b);
}

/* What each draft says about itself, gathered once.

   Every attribute is optional and "" means the draft never said — never that
   it said no. The distinction is the whole safety story here: silence must
   not convict, so an unstated brand can't conflict with anything. */
function profile(draft) {
  const body = bodyOf(draft);
  const specifics = body.item_specifics || [];
  const title = titleOf(draft);
  const words = titleWords(title);
  const tokens = new Set(words);
  // Falls back to the title only where the title can carry the answer
  // unambiguously: a colour word, or a size that is written as one.
  const sizeInTitle = title.toLowerCase().match(/\bs(?:ize|z)\.?\s+([a-z0-9]+(?:\.\d+)?)\b/);
  return {
    draft,
    tokens,
    lead: words[0] || "",
    brand: valueWords(body.brand || specificValue(specifics, "Brand")),
    color: valueWords(specificValue(specifics, "Color")
                      || specificValue(specifics, "Colour")
                      || words.filter((w) => COLORS.has(w)).join(" ")),
    size: valueWords(specificValue(specifics, "Size")
                     || (sizeInTitle ? sizeInTitle[1] : "")),
    model: valueWords(specificValue(specifics, "Model")
                      || specificValue(specifics, "Model Number")
                      || specificValue(specifics, "Style Code")),
    // The numbers the title itself carries, for the drafts whose specifics
    // never named a model: "Levis 501" against "Levis 505" is two items.
    numbers: numbersIn(tokens),
  };
}

/* Do these two drafts state something that says they are different items?

   Brands, models, sizes and colours are what a seller would look at to answer
   "are these the same jacket". When both drafts state one and the two answers
   share no word, they are describing two things — a Lacoste is not a Brooks
   Brothers, a Large is not a Medium — and nothing about their titles is going
   to change that. */
function contradict(a, b) {
  return ["brand", "model", "size", "color", "numbers"].some(
    (key) => conflicts(a[key], b[key]));
}

/* Brands neither draft stated outright, read off the front of the titles.

   A title's first meaningful word is where the brand goes — the AI writes
   them that way, and so do sellers. It is still a guess, so it only convicts
   when it is unopposed: each title leads with a word the other title never
   uses ANYWHERE. "Lacoste ..." against "Brooks Brothers ..." is two brands.
   "Nike Air Max ..." against "Air Max Nike ..." is one, said twice. */
function leadsDiffer(a, b) {
  if (a.brand.size && b.brand.size) return false;  // stated brands already ruled
  if (!a.lead || !b.lead || a.lead === b.lead) return false;
  return !b.tokens.has(a.lead) && !a.tokens.has(b.lead);
}

/* Titles that agree far more than they differ.

   Measured both ways round: against the shorter title, so a terse draft can
   still match its wordier twin, and against everything either one says, so a
   handful of shared words can't carry a pair whose titles are otherwise
   nothing alike. The old rule only had the first of those, which is how three
   category words out of a nine-word title read as a match. */
function sharesEnough(a, b) {
  if (a.tokens.size < 3 || b.tokens.size < 3) return false;
  const shared = [...a.tokens].filter((w) => b.tokens.has(w)).length;
  if (shared < 3) return false;
  const union = a.tokens.size + b.tokens.size - shared;
  return shared / Math.min(a.tokens.size, b.tokens.size) >= 0.5
    && shared / union >= 0.34;
}

/* Pairs of drafts worth asking the seller about, as [draftA, draftB].

   Nothing here decides anything: the answer is a hint pointing at Merge into
   one, and merging is the seller's call. */
export function duplicateSuspects(drafts) {
  // Profiled ONCE per draft, not once per pair: the comparison is already
  // quadratic, and re-reading both titles inside it made a big batch chew
  // through thousands of regex passes on every render.
  const profiles = (drafts || []).map(profile);
  const pairs = [];
  for (let i = 0; i < profiles.length; i++) {
    for (let j = i + 1; j < profiles.length; j++) {
      const a = profiles[i];
      const b = profiles[j];
      if (contradict(a, b) || leadsDiffer(a, b)) continue;
      if (sharesEnough(a, b)) pairs.push([a.draft, b.draft]);
    }
  }
  return pairs;
}

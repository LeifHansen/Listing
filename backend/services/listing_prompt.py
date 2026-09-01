"""The words that decide what a listing looks like.

The identify prompt lives here rather than beside the Anthropic client so the
rules can be read — and tested — without the SDK installed. CI deliberately
skips the heavy stack, so a test that imports services.claude_ai skips with it,
and a prompt rule nothing can assert on is a rule that quietly rots. Nothing
here imports anything, so nothing can make that true again.
"""
# eBay's well-known condition enum values (subset most listings use).
EBAY_CONDITIONS = [
    "NEW",
    "NEW_OTHER",
    "NEW_WITH_DEFECTS",
    "CERTIFIED_REFURBISHED",
    "SELLER_REFURBISHED",
    "LIKE_NEW",
    # eBay's apparel-only grades. The model is not asked to know which
    # categories take which — it grades the WEAR it can see, and the server
    # moves that grade onto the ladder the item's category actually offers
    # (taxonomy.nearest_allowed_condition). They are listed so a refine round
    # trip can echo one back without it being reset to USED_EXCELLENT.
    "PRE_OWNED_EXCELLENT",
    "PRE_OWNED_FAIR",
    "USED_EXCELLENT",
    "USED_VERY_GOOD",
    "USED_GOOD",
    "USED_ACCEPTABLE",
    "FOR_PARTS_OR_NOT_WORKING",
]

LISTING_SCHEMA = """
Return ONLY a JSON object (no markdown fences) with this exact shape:
{
  "title": "string, <= 80 chars, keyword-rich eBay title",
  "subtitle": "always the empty string \\"\\" (eBay charges an extra fee for subtitles; the seller adds one manually if they want)",
  "brand": "string",
  "condition": "one of: %s",
  "condition_description": "string describing visible wear/flaws",
  "category_suggestion": "human-readable eBay category path",
  "description": "string, 2-4 short paragraphs, buyer-friendly, no false claims, opening on the item itself (see the description rule below)",
  "price": number or null (suggested USD price based on item & condition),
  "purchase_price": number or null (ONLY the price on a store/thrift PRICE STICKER or price tag visible in the photos — what it costs to buy this item right now. null when no price sticker is legible. Never estimate; never confuse with the resale price above),
  "quantity": integer (default 1),
  "package_weight_oz": number (estimated TOTAL shipping weight in ounces, packed; best-effort estimate the seller can correct),
  "package_length_in": number (estimated SHIPPING BOX length in inches, packed),
  "package_width_in": number (estimated SHIPPING BOX width in inches, packed),
  "package_height_in": number (estimated SHIPPING BOX height in inches, packed),
  "item_specifics": [{"name": "string", "value": "string", "confidence": "high|medium"}],
  "missing_info": ["names of ITEM details a human should verify/fill, e.g. 'exact model number', 'size'. NEVER list where the item ships from, its location, shipping/return/payment policies, or handling time — the seller's account settles those once, not per listing"],
  "confidence": "low|medium|high",
  "raw_observations": "brief notes on what you actually see in the photos",
  "tags": [ {"photo": <1-based photo number>, "box": [x0, y0, x1, y1], "kind": "size|care|brand|model|barcode|other"} ]
}
Rules:
- Only state facts you can see or reasonably infer. Never invent serial numbers,
  authenticity guarantees, or specs you cannot verify; put those in missing_info.
- Title must be <= 80 characters, and its ORDER matters as much as its words.
  Lead with what identifies THIS item and nothing else, in this order:
  1. Brand, maker, artist or pattern name ("Royal Stafford", "Pyrex", "Levi's")
  2. The exact item name, model, pattern or number ("Sweetpea", "501", "441")
  3. What the thing is ("teacup & saucer", "mixing bowl", "straight-leg jeans")
  4. The specifics a buyer filters or searches on (size, colour, material,
     quantity, year)
  5. The condition wording a buyer scans for, when the item has earned it:
     NWT, NWOT, "new in box", "sealed", "unworn", "deadstock", "excellent
     condition". Only when it agrees with the condition field you return —
     never "excellent condition" on an item whose photos show wear, and
     never a tag claim ("NWT") without a tag visible in the photos.
  6. ONLY THEN the general descriptive words: vintage, antique, retro, rare,
     MCM, boho, unique, beautiful.
  Never START a title with a general word. "Vintage teacup" is a title
  thousands of listings share and it spends eBay's most heavily weighted
  position on nothing; "Royal Stafford Sweetpea teacup & saucer bone china
  vintage" reaches the buyer searching for that pattern by name. Keep those
  words — they earn their place at the end, not the front.
  80 characters is a budget, not a target: when the title runs long, cut from
  the BACK — the general words first, then the condition wording — never the
  brand, model or size at the front.
- Description: the FIRST WORDS must be item-specific — brand, then model or
  product name, then what the thing is ("Pyrex Cinderella 441 mixing bowl...",
  "Levi's 501 straight-leg jeans..."). NEVER open on a generic age or hype
  adjective: Vintage, Antique, Retro, Rare, Unique, Beautiful, Stunning,
  Gorgeous. Search weights the opening of a description most heavily and shows
  it as the result snippet, so a word that could head any listing in the store
  spends the highest-value position in the listing on nothing. Keep those words
  — just later in the sentence ("Pyrex Cinderella 441 mixing bowl, vintage
  1960s milk glass").
- ALWAYS estimate the packed shipping box dimensions (package_length_in,
  package_width_in, package_height_in) and weight — judge the item's real-world
  size from the photos and add a little room for packaging. Never leave the
  dimensions at 0; a reasonable estimate the seller can correct is required so
  shipping calculates. (e.g. a t-shirt ≈ 10×8×2 in; a coffee mug ≈ 6×5×5 in;
  a paperback ≈ 8×6×1 in; a pair of shoes in-box ≈ 13×8×5 in.)
- item_specifics: be thorough. Fill EVERY standard eBay item specific you can
  see or confidently infer, using eBay's exact aspect names as "name" (these
  populate the listing's item specifics, so more accurate entries = far better
  search visibility). Give ONE value per name; never guess. Common names by
  category:
  * Clothing: Department, Type, Style, Size, Size Type, Color, Material,
    Pattern, Sleeve Length, Fit, Neckline, Closure, Occasion, Season, Theme,
    Features, Country/Region of Manufacture, Vintage.
  * Shoes: Department, Type, Style, US Shoe Size, Color, Upper Material.
  * Trading cards: Game, Set, Card Name, Card Number, Language, Rarity, Finish,
    Features, Grade.
  * Collectibles/other: Type, Character, Material, Color, Theme, Year
    Manufactured, Country/Region of Manufacture.
  Use the canonical value eBay expects (e.g. Color "Red", Department "Men",
  Size "L"). For a field with two values, return it twice as separate entries
  (e.g. {"name":"Season","value":"Spring"} and {"name":"Season","value":"Summer"})
  rather than one comma-joined value. Put anything you cannot verify in
  missing_info instead of guessing.
  Read EVERYTHING legible in the photos before filling these: care tags, sewn
  labels, stamps, box/packaging text, model plates, and the human-readable
  digits printed under any barcode (that's the UPC/EAN; model numbers and MPN
  often sit nearby). Mark each entry's "confidence": "high" when you can
  literally read/see it or it's unambiguous, "medium" for a reasonable
  inference (fill those too — the seller sees a review flag on them). Never
  invent identifiers (UPC/EAN/ISBN/MPN/serial) you cannot actually read.
- tags: while examining the photos, note every TAG, LABEL, STAMP, or PRINTED
  MARKING worth reading up close: neck labels, waistband tags, care tags,
  shoe tongue/heel labels, hang tags, box text, model plates, barcodes.
  box is the tag's bounding region as FRACTIONS of that photo's width/height
  (x0,y0 = top-left, x1,y1 = bottom-right), padded a little so nothing is cut
  off. Include a tag even when you can't read it at this size — it will be
  zoomed in on later. At most 6 entries, best candidates first; no tags at
  all -> [].
""" % ", ".join(EBAY_CONDITIONS)


# The title/description ordering has to survive a refine too: a rewrite there
# reaches the same buyers and the same search snippet as the first draft, and
# "shorten this" is exactly the instruction that would otherwise trade the
# identifying words for the generic ones. Conditioned on the seller not asking
# otherwise — an explicit "start it with Vintage" is their call to make.
REFINE_ORDER_RULE = (
    "If you rewrite the title, it must still LEAD with brand or artist, "
    "then the exact model or pattern name, then what the thing is, then "
    "the specifics a buyer filters on (size, colour, material), then any "
    "condition wording, and keep general words like Vintage, Antique, "
    "Retro or Rare at the END. If you rewrite the description, its first "
    "words must stay item-specific — brand, model, what the thing is — and "
    "must never open on one of those general words. Both hold unless the "
    "seller's instruction explicitly asks for that opening. "
)

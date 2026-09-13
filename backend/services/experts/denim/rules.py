"""Vintage denim: the selvedge edge, the red tab, the patch, the lot code.

The denim expert's rule text, moved here verbatim from services/listing_prompt
when the verticals became experts. The words are unchanged and the constant
names are unchanged -- listing_prompt still re-exports them, so every pass and
every test that already reads VINTAGE_DENIM_RULE keeps reading the same string.

What moved is only WHERE the text lives: with the expert that owns it, beside
the scorer that decides whether an item is denim at all. Nothing here imports
anything, for the reason listing_prompt gives in its own docstring -- a rule
nothing can assert on is a rule that quietly rots, and CI's light job proves
these are assertable with no heavy stack installed.
"""

# --- vintage denim: the selvedge edge, the red tab, the patch, the lot code --
#
# A pair of Levi's 501s is a $30 listing or a $3,000 one, and the same handful
# of details decides which: the lettering on the red tab, the edge of the
# fabric along the outseam, the wording on the patch, the row of numbers on
# the care tag, the stamp on the back of the top button, the rivets inside the
# back pockets. Every one of them is in frame in a normal set of photos, and
# every one is searched for BY NAME by the buyers who pay the premium — "Big
# E", "redline selvedge", "501XX", "hidden rivets", "single stitch". A draft
# that says "Vintage Levi's 501 jeans" about a Big E redline pair has left
# most of its price in the pocket.
#
# The sticker rule above already says "read every tag". What it could not say
# is what to do with a fabric edge, which is not a tag and is not text: a
# selvedge outseam is a woven detail visible only where a hem is turned up,
# and a model that has not been told to look there reports the hem as a hem.
# So this rule names each place to look, what each one looks like, and the
# year each one supports — the facts are the ones every collector's guide
# prints, and they are stated with the ranges those guides agree on rather
# than to the year, because a rule that dates a pair too tightly is a false
# claim in the listing.
#
# Two things ride along with it, for the same reason the sticker rule carries
# its own:
#
#   * every marker is a PRICE claim, so a marker that is not in the photos is
#     never written. "Selvedge" on a pair whose hem was never turned up is not
#     a guess a buyer forgives on arrival; it is a return and a defect.
#   * the size on the patch is not the size of the pair. Shrink-to-Fit denim
#     has shrunk, and the tag size sold as the real one comes back too.
VINTAGE_DENIM_RULE = """
- JEANS AND DENIM JACKETS — LEVI'S ABOVE ALL — ARE DATED BY THEIR HARDWARE AND
  TAGS, AND THE DATE IS MOST OF THE PRICE. A pair of Levi's 501s is a $30
  listing or a $3,000 one, and the same handful of details decides which.
  Collectors search for those details BY NAME ("Big E", "redline selvedge",
  "501XX", "hidden rivets", "single stitch"), so every one you can see
  belongs in the title or the description, and every one you cannot see must
  NOT be claimed. Check each of the following and say what you found:
  * THE SELVEDGE EDGE. Go to the OUTSEAM (the outer leg seam). At a cuffed or
    turned-up hem, or wherever the inside of the leg shows, look at the edge
    of the fabric along that seam. SELVEDGE is a clean, self-finished edge: a
    narrow woven band, usually white, that needs no overlock stitch, often
    with a coloured thread running along it. On Levi's that thread is red —
    "REDLINE". NON-selvedge is a raw edge sealed with a zigzag overlock
    (serged) stitch and loose threads. Levi's made 501s on Cone Mills redline
    selvedge until it was phased out in the early-to-mid 1980s, so a redline
    outseam on a US-made pair says "before about 1986" and is worth several
    times a non-selvedge pair. Also check the coin pocket edge and the inside
    of the fly: on some selvedge-era pairs those show a selvedge edge too.
    Selvedge is NOT Levi's-only and NOT vintage-only: Levi's Vintage Clothing
    reproductions (1990s onward), Japanese makers (Evisu, Sugar Cane, Full
    Count, Iron Heart, Momotaro), and Lee and Wrangler reissues all use it,
    and it is a high-value word for all of them — so name the brand from the
    patch and tab, never from the selvedge. Say "selvedge" ONLY when the edge
    is in frame. A hem that is not turned up shows nothing; "selvedge" is
    then a missing_info item ("turn up the hem and photograph the outseam
    edge"), not a claim.
  * THE RED TAB on the right back pocket, introduced in 1936: no tab at all
    on a pair that should have one is either pre-1936 or a removed tab, and
    the empty stitch holes cannot tell you which. Read the tab as THREE
    separate facts, because each dates a pair on its own:
    - THE LETTERING. "LEVI'S" in ALL CAPITALS — the "BIG E" — was used until
      1971; "Levi's" with a lowercase e is 1971 onward and still current. Big
      E is the most searched vintage Levi's term and carries a large premium.
      But a capital E is NOT proof of a vintage pair on its own: Levi's
      Vintage Clothing reproductions use it, and from 2018 so does the Levi's
      Premium line. What separates them is the INSIDE of the garment — a
      modern care tag, a four-digit MMYY date code, a "Made in Japan" or
      post-2003 label all say the tab is a reproduction of a Big E and not
      one. So write "Big E" in the title when you can read a capital E AND
      nothing inside the pair contradicts it; when the inside says modern,
      say "Big E tab (Levi's Premium/LVC reproduction)" instead, which is
      still what that buyer searches. Never write it when you cannot read
      the letter.
    - WHICH FACES ARE LETTERED. The oldest tabs are lettered on ONE SIDE
      ONLY: from 1936 until the early 1950s, "LEVI'S" was stitched on a
      single face and the reverse is blank. Around 1951-1954 Levi's went to
      a DOUBLE-SIDED tab, lettered on both faces, and that is what nearly
      every pair since has. So a single-sided tab on an otherwise old pair
      is the EARLIEST tab there is and worth saying — but check it against
      the rest, because single-sided tabs came BACK in the mid-1980s: a
      single-sided tab beside a lowercase e, or a care tag, is a 1980s pair
      or later, not a 1940s one. Say which faces you can actually see, and
      when only one face is in frame say exactly that rather than calling it
      single-sided.
    - THE ® MARK. The registered-trademark mark arrived on the tab with the
      double-sided change in the early 1950s. "LEVI'S" with NO ® beside it
      is earlier than "LEVI'S®". Transcribe any ®, ™ or © exactly where you
      see it.
    A tab with the ® AND NO NAME on it is a modern trademark-only tab —
    Levi's makes a share of its tabs blank to keep the mark in use — so it
    dates nothing and is never "Big E". It is also the one tab LVC does not
    use, so a blank tab rules a reproduction OUT rather than in.
    THE TAB'S COLOUR names the LINE, and the line is not the era:
    - ORANGE tab: Levi's fashion line from the 1960s until 1999 (bell
      bottoms, flares and boot cuts such as the 646 and 517). A different
      line from red tab, not a lesser one, and searched by name.
    - WHITE tab: corduroy jeans and jackets, and "Levi's for Gals", the
      first women's line — 1960s and 1970s, so a white tab is NOT a sign of
      a late pair and must not be described as one.
    - SILVER tab: the loose and baggy line from the late 1980s through the
      1990s, searched by name ("SilverTab") by buyers who want exactly that
      cut.
    - BLACK, and anything else: read the patch for the lot and say what you
      see rather than assigning an era.
  * THE PATCH on the back waistband. Transcribe every word and number on it
    VERBATIM. Real LEATHER (creased, cracked, hair side) was used until the
    mid-1950s; after that the patch is "leather-look" card, which is what
    most vintage pairs have. The LOT number is on the patch: "501", "501XX",
    "505", "517", "550", "646", "LOT 501". "XX" after the lot means the
    1966-68 pairs or earlier and is a very valuable mark (Levi's Vintage
    Clothing put it back on reproductions from 1987 on, and those carry a
    modern inside label saying so). Older patches say "Every Garment
    Guaranteed"; later ones drop it. The W and L on the patch are the TAG
    size, never the actual size (see sizing below).
  * THE INSIDE CARE TAG, sewn into the waistband or a back pocket seam. NO
    care tag at all means before about 1971-73, when they were introduced
    (or a removed one — say which you see). When there is one, transcribe
    the whole number row on it exactly as printed, then read it as:
    - the LOT-AND-FINISH code, "501-0115" style: the number before the dash
      is the lot (the fit); the four digits after it are the fabric FINISH
      or wash, NOT a size and NOT a date. 0000 is rigid Shrink-to-Fit indigo,
      0115 the pre-shrunk indigo stonewash, 0660 black. Give the code as
      printed and name the finish only when you know it.
    - the SIZE: "W32 L34" or "32 34" — the tag size.
    - "WPL 423": Levi Strauss & Co.'s registered wool-products label number,
      on every genuine US-made care tag.
    - the PRODUCTION CODE. 1970s and 1980s tags carry a month digit and a
      year digit (3 and 7 read as March 1977 or March 1987 — the decade is
      settled by the other markers, never by the tag alone); from about 1993
      the date is a four-digit MMYY code (0496 = April 1996). A separate
      three-digit FACTORY number, often on the back of the tag, should match
      the stamp on the back of the top button. Give the reading WITH the
      digits it came from ("care tag row 0496, read as April 1996") so the
      seller can check it, and never read a date into digits you cannot see.
    - "MADE IN U.S.A." Levi's closed its last US plants in 2002-2003: a
      US-made pair is 2003 or earlier, and a US-made NON-selvedge 501 is
      roughly mid-1980s to 2003.
  * THE BACK OF THE TOP BUTTON. A number is stamped on the reverse of the
    waist button: the FACTORY code. 555 is the Valencia Street plant, San
    Francisco (also on 1990s-2002 LVC reproductions); 524 El Paso, Texas;
    554 San Antonio, Texas; 553 North Carolina. Single- and two-digit stamps
    are older US plants. Transcribe the stamp exactly and treat it as
    confirmation of the care-tag factory number, never as a build year on
    its own. The button face reads "LEVI STRAUSS & CO. S.F. CAL." on vintage
    pairs and the rivets are stamped "L.S.&CO. S.F." — read both.
  * THE BACK POCKETS. Turn one inside out: copper HIDDEN RIVETS at the pocket
    corners, covered by the fabric, were used from 1937 until about 1966,
    when bar tacks replaced them; exposed back-pocket rivets are before 1937.
    The ARCUATE (the double-arc stitch across the pocket) is SINGLE-NEEDLE —
    one row of thread meeting in a point at the centre — before about 1947
    and double-needle after; a painted-on arcuate is a WWII pair (1942-47).
    Look inside the pocket bag for a stamped or printed lot number too.
  * THE FLY. 501 is a BUTTON fly; a zip fly is a different lot (505, from
    1967, or 502/501Z) — read the patch, do not assume. A V-shaped stitch
    beside the top button is an older mark (roughly before 1970). Read the
    zipper pull's brand: Talon, Scovill and Gripper date a pair; YKK is
    later.
  REPORTING. In raw_observations, list each marker you checked and what it
  showed, INCLUDING the ones you could not see ("hem not turned up — selvedge
  unknown"). In the description's Key Details, write the markers by their
  collector names with the year each supports. In the title, after the brand
  and lot ("Levi's 501XX"), put the markers that identify THIS pair in the
  order buyers search them — "Big E", "Selvedge" or "Redline Selvedge",
  "Hidden Rivets", "Single Stitch" — then the tag size, then "USA" and the
  era. In item_specifics, answer Model or Product Line with the lot ("501"),
  Closure ("Button"), Fabric Type / Features / Wash with the eBay value that
  says selvedge or names the finish you saw, Country/Region of Manufacture,
  and Era / Decade / Vintage from the dating above. Date the pair to the era
  the markers support and no tighter; when markers disagree (a Big E tab on
  a pair with a 1990s care tag is a reproduction or a swapped tab), say so
  and put the era in missing_info. Never write "Big E", "selvedge", "XX",
  "hidden rivets" or "single stitch" about a detail that is not in the
  photos: each is a price claim a buyer will check on arrival.
  SIZING. Vintage Levi's were Shrink-to-Fit and have shrunk: the W/L on the
  patch is the TAG size, and the pair now measures one to three inches
  smaller. Always give the tag size as "Tag size W32 L34", and the actual
  size ONLY from a tape measure in the photos (waist flat x2, inseam, rise,
  leg opening); otherwise put "measured waist, inseam and rise" in
  missing_info. Collectors buy on measurements, and a tag size sold as the
  real one comes back.
"""

# What the tag LOCATOR is told, separately: it draws boxes, it does not read,
# and the thing it has to be told is that on jeans the facts are on hardware
# and on a fabric edge, neither of which looks like a tag.
DENIM_TAG_SCAN_RULE = """
- JEANS AND DENIM JACKETS (Levi's, Lee, Wrangler and the rest) carry their
  facts on HARDWARE as much as on tags, and each of these is worth a box: the
  RED TAB on the back pocket (its lettering decides the decade); the PATCH on
  the waistband; the INSIDE CARE TAG and its row of numbers; the BACK of the
  top button (a factory number is stamped there); the rivets; the coin pocket
  edge; and the OUTSEAM EDGE wherever a hem is turned up or a leg is turned
  out — a selvedge edge there is the most valuable detail on the pair, and it
  is only ever visible at that spot. Box it as "selvedge" even when it is a
  sliver of the photo, and box the tab and the patch as "tab" and "patch".
"""

# What the zoom-and-transcribe pass writes for denim, one marker per line, so
# the specifics fill can quote the tab, the lot and the button stamp as
# ground truth the way it quotes a barcode.
DENIM_TRANSCRIBE_LINES = (
    "For JEANS AND DENIM, add one line per marker you can see, exactly as "
    "read: 'RED TAB: <the lettering as printed; whether the E is a capital "
    "(Big E) or lowercase; whether an \u00ae, \u2122 or \u00a9 is beside it; and which "
    "faces are lettered \u2014 one side only, both sides, or only one side "
    "visible>', 'PATCH: <every word and number>', "
    "'LOT: <the lot-finish code as printed>', 'CARE TAG ROW: <the digits as "
    "printed>, read as <month / year / factory>', 'BUTTON BACK: <stamp>', "
    "'RIVETS: <stamp>', 'SELVEDGE: <redline, plain selvedge or overlocked, "
    "and where you saw it>', 'ARCUATE: <single or double needle>'. A marker "
    "you looked for and could not see is a line too ('SELVEDGE: not visible, "
    "hem not turned up').\n\n"
)

# --- denim in a pile: the front and the back of one pair ---------------------
#
# The rules above tell a pass what to READ on a pair of jeans. This one tells
# the grouping passes how a pair ARRIVES, which is the thing they kept getting
# wrong: denim is shot laid flat, one pair at a time, front first and then the
# same pair turned over. Every pair in a pile is therefore TWO photos, and a
# seller with six pairs uploads twelve.
#
# Bulk mode read that pile as twelve items. The reason is in the pictures: the
# front and the back of one pair of jeans look nothing alike -- the front is a
# fly, a coin pocket and a top button, the back is a yoke, two arcuate-stitched
# pockets, a red tab and a leather patch -- and every rule the grouping pass
# had pushed the same way. "Identity evidence outranks looks" is true, and on
# denim every identity mark is on the BACK, so a front and its own back read as
# one photo with marks and one without; "count the tags and patches you can see
# and expect at least that many items" counted the tab, the patch and the care
# tag of ONE pair as three. The result was two live eBay listings for one pair
# of jeans -- the worst outcome bulk mode has -- and the halves scattered, a
# front under one draft and its back under another.
#
# So the grouping passes are told the shape of the upload, and told it where
# they would otherwise infer the opposite:
#
#   * the two views of one pair look different BY DESIGN, and that difference
#     is never evidence of a second pair;
#   * pairs are counted by BACKS, never by how many markers are in frame;
#   * a second pair is a second BACK whose patch reads differently, quoted;
#   * the back that follows a front is the back OF that front, so the pair's
#     own order is kept and no photo is moved between pairs. A back filed under
#     the wrong pair puts the wrong lot and the wrong W/L size on two listings,
#     and nothing downstream can see that it happened.
DENIM_FRONT_AND_BACK_RULE = """
- JEANS ARE SHOT FRONT AND BACK, AND BOTH SHOTS ARE ONE LISTING. Denim is
  photographed laid flat, one pair at a time: the front, then the SAME pair
  turned over. Every pair in the pile is therefore at least two photos, a
  FRONT and a BACK of one item, and a pile of six pairs is six listings, not
  twelve. A back view is never an item of its own.
  * THE TWO VIEWS OF ONE PAIR LOOK NOTHING ALIKE, AND THAT IS NORMAL. The
    front shows the fly, the top button, the coin pocket and the front
    pockets; the back shows the yoke, the two back pockets and their arcuate
    stitching, the RED TAB and the leather PATCH. Every marker that names a
    pair -- tab, patch, lot number, W/L size -- is on the BACK, so the front
    of a pair carries none of them. One pair photographed from both sides is
    SUPPOSED to look like this. It is NOT evidence of two items.
  * COUNT PAIRS BY BACKS, NOT BY MARKERS. The number of pairs is the number of
    BACK views -- equivalently, the number of distinct patches -- never the
    number of tags, tabs and labels in frame: one pair shows a tab AND a patch
    AND a care tag and is still one pair. A photo with no patch in it is the
    FRONT of a pair whose back you have already counted, not an item whose tag
    is missing.
  * A SECOND PAIR IS A SECOND BACK THAT READS DIFFERENTLY. Two pairs are two
    listings when two BACK views show patch, lot, tab or size text that reads
    differently ("501 W32 L34" against "505 W34 L32"), or a plainly different
    wash, fade, hem or repair. Quote both readings as the evidence. Two backs
    that read the SAME, or two you cannot read, stay ONE listing: the seller
    can drag a spare photo out of a draft in a second, and nobody can undo two
    live eBay listings for one pair.
  * WHEN YOU ARE SHOWN ONE PHOTO PER GROUP, the front and the back of one
    pair are the two groups to put back together: a group whose photo is a
    BACK belongs with the group holding the FRONT it was shot with, which is
    almost always the group immediately before it. The reverse is NOT true --
    several groups each showing a FRONT are several pairs, and their backs
    are simply not in front of you. Never merge two fronts because neither
    one shows a patch.
  * KEEP EACH PAIR'S OWN ORDER AND NEVER MOVE A PHOTO BETWEEN PAIRS. The back
    that follows a front is the back OF that front. List a pair's photos in
    the order they were shot -- the front, then that front's back, then the
    close-ups taken with them -- and never collect the fronts into one item
    and the backs into another, or hand a pair the back of the pair beside it.
    A swapped back puts the wrong lot, the wrong era and the wrong W/L size on
    two listings at once, and neither the seller nor the buyer can see from
    the photos that it went wrong.
"""

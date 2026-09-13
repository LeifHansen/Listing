"""Art: the signature, the edition number, the chop, the plate mark.

The art expert's rule text, moved here verbatim from services/listing_prompt
when the verticals became experts. The words are unchanged and the constant
names are unchanged -- listing_prompt still re-exports them, so every pass and
every test that already reads ART_RULE or BLANK_CANVAS_RULE keeps reading the
same string.

What moved is only WHERE the text lives: with the expert that owns it, beside
the roster, the sources and the scorer. Nothing here imports anything, for the
reason listing_prompt gives in its own docstring -- a rule nothing can assert
on is a rule that quietly rots, and CI's light job proves these are assertable
with no heavy stack installed.
"""

# --- art: the signature, the edition number, the chop, the plate mark ------
#
# A print is a $15 listing or a $1,500 one, and the difference is written in
# pencil in the bottom margin: a hand signature at the lower right, an edition
# fraction at the lower left, an embossed chop beside them. A painting is an
# original or a canvas print, and the difference is a surface the camera can
# see: brushstrokes and canvas weave, or a flat uniform sheet with the strokes
# printed on. Every one of those marks is in frame in an ordinary set of
# photos, every one is searched for by name ("hand signed", "numbered",
# "artist proof", "original oil"), and the identify prompt said to look for
# them in one sentence buried in a rule about autographs and trading cards.
#
# What the app kept doing was reading the image and not the margin: a signed
# and numbered lithograph drafted as "Vintage Art Print", no artist, no
# "signed", no edition, because a pencil signature is thirty pixels tall in a
# whole-frame photo and nothing told the pass that the small grey scrawl under
# the picture is the most valuable thing in it. The sticker rule could not
# say so: a pencil signature is not a sticker and not printed, a blind stamp
# is embossed and colourless, a plate mark is a dent in the paper, and a
# label on the back is a photo the seller has to be asked for. So this rule
# names each place to look, what each mark looks like, what each one
# establishes, and how to write it into the listing -- and it rides with the
# identify pass, the tag locator, the zoom-and-transcribe pass, the specifics
# fill and the art lookup, as one text, exactly as the denim rule does.
#
# Two things ride along with it, for the same reason the other rules carry
# their own:
#
#   * every mark is a PRICE claim, so a mark that is not in the photos is
#     never claimed and never denied. "Unsigned" about a print whose margin
#     is under the mat is as false as "signed" would be; the honest line is
#     "signature not visible in these photos" and a request for the photo.
#   * a signature is READ, letter by letter, never completed into a name the
#     letters do not spell. Naming the wrong artist is the one error a buyer
#     never forgives, and hedging the right one into "style of" costs the
#     seller most of the price. Both are avoided the same way: transcribe
#     what is there, then say which artist it points to and why.
ART_RULE = """
- ART -- PRINTS, PAINTINGS, DRAWINGS, PHOTOGRAPHS AND SCULPTURE -- IS
  IDENTIFIED BY ITS SIGNATURE, ITS EDITION NUMBER AND ITS SURFACE, AND THOSE
  ARE MOST OF THE PRICE. A signed and numbered lithograph and a poster of the
  same picture look identical from across the room and differ in price by a
  hundred times. The facts that separate them are small, faint, in pencil,
  embossed, or on the back, and every one of them is worth a close look.
  Read the MARGIN and the BACK before you read the picture. Check each of the
  following and say what you found:
  * THE SIGNATURE. Look in the bottom margin below the image, at the LOWER
    RIGHT first, then the lower left and the lower edge of the image itself,
    then the back. A HAND signature sits ON the paper: pencil (grey, with a
    graphite sheen, slightly indented), ink or paint, and it does not share
    the dot pattern or ink layer of the printed picture. A signature IN THE
    PLATE (also "in the stone", "in the screen") is part of the printed image
    -- same ink, same dots, usually inside the picture area -- and it means
    the artist signed the ORIGINAL, not this sheet; it is not "hand signed".
    A signature on a painting is usually a lower corner, in paint; on a
    sculpture it is on the base or the back, with the edition and often a
    foundry mark. Transcribe the signature LETTER BY LETTER as it reads,
    where it is, and what it is made with. When the letters spell a name,
    that name is the ARTIST: put it first in the title, in brand, and in an
    Artist item specific. When you can read only part of it, give the
    letters you can read, then -- separately, in raw_observations and
    missing_info, never in the title -- the artist the letters, the hand and
    the image together most plausibly point to, as a reading for the seller
    to confirm. Never write a name the letters do not support, and never
    resolve an unclear signature by dropping it: "signed in pencil lower
    right, reads 'J. W...', signature partly legible" is the honest line.
  * THE EDITION NUMBER. Look at the LOWER LEFT of the margin. A fraction --
    "84/250", "12/75", "XX/L" in Roman numerals -- is this sheet's number
    over the edition size: a NUMBERED LIMITED EDITION, and 250 is the
    Edition Size. The letters that take its place are also editions and are
    searched by name: "A/P" or "AP" (artist's proof), "E/A" (epreuve
    d'artiste, the same in French), "H/C" (hors commerce, not for sale),
    "P/P" (printer's proof), "T/P" (trial proof), "B.A.T." (bon a tirer, the
    approved proof), sometimes with their own count ("A/P 3/20"). Transcribe
    the annotation EXACTLY as written, including the slash and any letters.
    A pencil fraction beside a pencil signature is the strongest evidence a
    print is a hand-signed limited edition; a fraction printed in the same
    ink as the image is a printed reproduction of one and is reported as
    such. A print with no fraction is not thereby an open edition: say
    "edition number not visible" and ask for the margin.
  * THE TITLE ON THE SHEET. Many signed editions carry the work's title in
    pencil in the CENTRE of the lower margin, between the number and the
    signature. Read it: it is the name collectors search for, and it goes in
    the title right after the artist.
  * CHOPS, BLIND STAMPS AND INK STAMPS. A BLIND STAMP (chop mark) is an
    EMBOSSED, colourless mark pressed into the paper, usually in a lower
    corner of the margin, sometimes only visible as a raised shape in raking
    light: the printer's, publisher's or artist's mark. Well-known workshop
    chops (Tamarind, Gemini G.E.L., Mourlot, ULAE, Tyler Graphics, Cirrus,
    Landfall, Pace Editions) date and authenticate an edition, so read and
    name the chop when you can and box it for the zoom when you cannot. An
    INK stamp on the back is an estate, gallery, publisher or collection
    stamp -- transcribe it.
  * THE PLATE MARK. An INTAGLIO print (etching, engraving, aquatint,
    drypoint, mezzotint) leaves a rectangular INDENTATION in the paper around
    the image, where the plate was pressed in under the press: a soft ridge
    a few millimetres outside the picture, often with a faint tone inside it.
    Lithographs, screenprints, woodcuts and offset reproductions have none.
    A plate mark with a pencil signature is an original etching; a plate
    mark on a picture whose surface is halftone dots is a reproduction with
    a fake plate mark, and worth saying.
  * THE SURFACE, up close. This is how an ORIGINAL is told from a PRINT and a
    hand-pulled print from a reproduction, and it is visible in any photo
    taken close enough:
    - brushstrokes with relief, impasto, paint that changes sheen, canvas
      weave showing through thin paint, paint over the tacking edge: an
      ORIGINAL PAINTING;
    - pooled and bled washes, pencil under-drawing, the tooth of the paper
      holding pigment, graphite or charcoal or pastel dust: an ORIGINAL
      WATERCOLOR or DRAWING;
    - flat, slightly raised layers of opaque ink with crisp edges and no
      dots: a SERIGRAPH / screenprint;
    - a soft grainy crayon texture with no dot pattern: a hand-pulled stone
      or plate LITHOGRAPH;
    - a fine random spray of tiny dots in several colours: an inkjet /
      GICLEE, usually an open or limited-edition reproduction;
    - a regular grid or rosette of halftone DOTS, the same everywhere
      including in the signature: an OFFSET reproduction -- a poster or an
      open-edition print;
    - a perfectly uniform sheet with printed "strokes" that cast no shadow,
      or a canvas whose image wraps around the stretcher with the picture's
      edge mirrored: a CANVAS PRINT, not a painting.
    Say which surface you see. A picture you cannot get close enough to
    judge is "surface not visible at this size", never "print" by default.
  * THE PRINTED LINES ALONG THE EDGE. A credit line printed under the image
    ("(c) 1987 Artist Name / Publisher, Inc.", "Printed in Italy", a museum
    and exhibition dates, a poster shop's name) names the PUBLISHER and
    usually the YEAR of this printing and marks an open-edition print or
    exhibition poster -- unless a pencil signature and number are ALSO there,
    which makes it a signed poster or a publisher's signed edition, and both
    are searched for by name. Transcribe every printed line verbatim,
    including the copyright year. The publisher is never the brand: the
    artist is.
  * THE BACK AND THE FRAME. Gallery labels, framer's labels, exhibition and
    auction labels, inventory numbers, a certificate of authenticity in a
    sleeve, an artist's inscription, a publisher's stamp, an old price: all
    of these are on the verso or the frame back, and each one is a fact for
    the listing. Read every label verbatim. Note the paper too: a deckled
    (feathered, hand-torn) edge, a watermark (Arches, Rives BFK, Fabriano,
    Somerset), foxing (brown spots) and toning all say something about the
    edition and the age, and each is a word buyers search.
  REPORTING. In raw_observations, write one line per mark you checked --
  SIGNATURE, EDITION, TITLE ON SHEET, STAMP, PLATE MARK, CAPTION, LABEL,
  SURFACE -- with what it showed, INCLUDING the ones you could not see
  ("EDITION: margin hidden under the mat -- not visible"). In the title, lead
  with the ARTIST'S NAME as it is catalogued (first name then surname:
  "Salvador Dali", "Marc Chagall"), then the TITLE of the work when you have
  it, then the medium by its collector name (lithograph, serigraph, etching,
  woodblock, giclee, oil on canvas, watercolor), then the words that price
  it in the order buyers type them -- "Hand Signed", "Signed & Numbered
  84/250", "Artist Proof", "Original" -- then framed/matted and the size:
  "Salvador Dali Lincoln in Dalivision Lithograph Hand Signed Numbered
  84/250 Framed". Put the artist in brand and in an "Artist" item specific,
  and answer the art specifics: Signed (Yes only for a hand signature you
  can see, never for one in the plate), Signed By, Edition Type ("Limited
  Edition" with a fraction, "Open Edition" only when a printed credit line
  and no number say so, "Artist Proof" for an A/P), Edition Size (the number
  under the slash), Print Type or Production Technique (the medium), Type
  (Print / Painting / Drawing / Photograph / Sculpture), Original/Licensed
  Reprint or Original/Reproduction (Original for a surface that is paint or
  a hand-pulled print; never Reproduction to be safe), Year Produced (only a
  year written on the piece), Subject, Style, Material (Paper / Canvas /
  Board), Features (Numbered, Signed, Framed, Matted, Certificate of
  Authenticity -- only the ones in the photos), and the sheet or frame size
  from a tape in the photos. In the description's Key Details, write the
  signature, the edition, the chop and the labels in the words you read
  them. Never write "hand signed", "numbered", "artist proof", "original" or
  a chop's name about a mark that is not in the photos: each is a price
  claim a buyer checks on arrival. Never write "unsigned", "open edition",
  "poster" or "reproduction" about a piece whose margin, back or surface
  you cannot see: that is the same false claim in the cheaper direction.
  When a mark is out of frame, ask for it in missing_info by name
  ("photograph the lower margin close up, both corners", "photograph the
  back of the frame and any labels", "photograph the surface at an angle in
  raking light").
"""

# What the tag LOCATOR is told, separately: it draws boxes, it does not read,
# and the thing it has to be told is that on art the facts are in pencil in
# a margin, embossed into paper and on labels round the back -- none of which
# looks like a tag, and the first of which is nearly invisible at scan size.
ART_TAG_SCAN_RULE = """
- ART -- prints, paintings, drawings, photographs, sculpture -- carries its
  facts in the MARGINS and on the BACK, in pencil, in blind embossing and on
  paper labels, none of which looks like a tag, and each of these is worth a
  box: the SIGNATURE in the bottom margin (usually lower right, below the
  image, sometimes a lower corner of the picture or the base of a sculpture)
  as "signature"; the EDITION fraction or A/P annotation (usually lower left)
  as "edition"; any embossed chop, blind stamp or ink stamp as "stamp"; a
  printed title, credit, copyright or publisher line along the bottom edge as
  "caption"; and every gallery, framer, publisher, auction or certificate
  label on the back or the frame as "label". A pencil signature is a faint
  grey scrawl a few pixels tall at this size and the single most valuable
  thing in the photo -- box it anyway, generously, with the whole bottom
  margin if you cannot tell where in it the writing is; that is what the zoom
  is for. When a photo shows the BACK of a canvas or a frame, that side is
  all marks and each is worth a box: any writing on the fabric or the bars
  -- an artist's inscription, a title, a date, a number, an old price -- as
  "signature" when it reads as a name and "label" otherwise; the mill's
  stamp on a stretcher bar or along the canvas selvedge as "stamp"; and
  every gallery, exhibition, framer or auction label as "label".
"""

# What the zoom-and-transcribe pass writes for art, one mark per line, so the
# specifics fill can quote the signature, the edition and the chop as ground
# truth the way it quotes a barcode.
ART_TRANSCRIBE_LINES = (
    "For ART, add one line per mark you can see, exactly as read: "
    "'SIGNATURE: <the letters as they read, what it is written with "
    "(pencil / ink / paint), where (lower right margin / in the image / on "
    "the back), and whether it is hand-written on the paper or printed as "
    "part of the image (in the plate)>', 'EDITION: <the fraction or "
    "annotation exactly as written -- 84/250, A/P, H/C, E/A, P/P -- and "
    "where>', 'TITLE ON SHEET: <as written>', 'STAMP: <what an embossed "
    "chop or an ink stamp says or shows, and where>', 'CAPTION: <every "
    "printed credit, copyright or publisher line verbatim, with its "
    "year>', 'LABEL: <each gallery, framer, publisher, auction or "
    "certificate label, verbatim>', 'PLATE MARK: <present / not visible>', "
    "'SUPPORT: <a mill or grade stamp on a stretcher bar or a canvas "
    "selvedge, verbatim -- it names who made the canvas, never who painted "
    "it>', 'VERSO: <which side of the piece this crop shows, and every "
    "inscription, label, stencil, inventory or lot number on the back, "
    "verbatim>', "
    "'SURFACE: <brushstrokes and canvas weave / flat ink layers / grainy "
    "crayon texture / inkjet spray / halftone dots / cannot tell>'. A mark "
    "you looked for and could not see is a line too ('EDITION: not visible "
    "in these crops'). Read a signature letter by letter and never complete "
    "it into a name the letters do not spell; when it points to a known "
    "artist, say so on the same line, as a reading to confirm.\n\n"
)


# --- the blank canvas that is the back of a painting ------------------------
#
# A seller photographed a stretched canvas from behind -- pale fabric, wooden
# stretcher bars around it, the fabric folded and stapled over the edges,
# "Gronda" stamped along the bottom bar -- and the app drafted "Gronda Blank
# Stretched Artist Canvas on Wood Frame Fabric Wrapped Edges", $17.99, with
# the canvas mill as the brand. Every word of it was read off the photo
# correctly and the listing was still worthless, because the photo was THE
# BACK OF A PAINTING and the picture was on the other side.
#
# Nobody lists one used blank canvas. They are a few dollars new, they sell
# in shrink-wrapped multipacks, and a second-hand one costs more to ship than
# it fetches -- so a listing for one is never the right answer, and "blank
# canvas" is not a draft this app has any business writing. What people
# photograph is the thing they own and mean to sell, and when that thing is a
# painting the back of it is in the set: the back is where the artist wrote
# the title, where the gallery stapled its label, and where every guide tells
# a seller to look. The back of a stretched painting looks exactly like a
# blank canvas, because that is what it is -- unpainted fabric -- and the
# mill's stamp is on the bar of a canvas whether or not anyone ever painted
# on the front of it.
#
# So the judgement this rule asks for is not "is the canvas blank", which
# from behind it always is, but WHICH SIDE AM I LOOKING AT -- and that is
# written plainly in the photo: stretcher bars standing proud around a
# recessed field of fabric, staples and folded corners, a cross-brace, a
# hanging wire. All of those are the back. A front is flush, and a front has
# a picture.
#
# The remedy is the one ART_RULE already uses for a margin hidden under a
# mat: read what the back does say -- inscription, title, date, labels,
# numbers, all of which are worth more than the fabric -- claim nothing about
# a picture nobody has photographed, and ask for the front by name. The same
# error has a framed twin (the back of a framed picture drafted as an empty
# frame), and it is refused here on the same terms.
BLANK_CANVAS_RULE = """
- A BLANK CANVAS IS THE BACK OF A PAINTING. When the photos show stretched
  fabric with no picture on it -- bare canvas or linen over wooden stretcher
  bars -- YOU ARE LOOKING AT THE BACK OF A WORK OF ART. You are not looking
  at an unpainted canvas for sale, and "blank canvas", "unused canvas",
  "artist canvas" or "canvas panel" is NEVER the item. A blank canvas is a
  few dollars of art supply that nobody photographs one at a time to sell;
  a painting photographed from behind is an everyday thing, because the back
  is where the artist wrote the title and the gallery put its label. If you
  find yourself about to draft a blank canvas, you have the piece BACK TO
  FRONT -- say so and treat it as art.
  * YOU ARE LOOKING AT THE BACK when you can see any of these, and one is
    enough: four wooden STRETCHER BARS standing proud around a field of
    fabric that is RECESSED behind them (from the front a canvas is flush
    and carries a picture); the fabric FOLDED, STAPLED or TACKED over the
    edges and pleated at the corners; a CROSS-BRACE or centre strut across
    the opening; keys or wedges in the corners; HANGING HARDWARE -- wire,
    D-rings, screw eyes, sawtooth hanger, bumper pads; a mill's stamp or
    grade label on a bar or on the fabric; paint that has WRAPPED OVER the
    tacking edge, or a faint bloom of the picture pushing through the weave
    from the other side; dust, toning, foxing or a grubby edge that no new
    canvas has.
  * THE MILL IS NOT THE BRAND AND NOT THE ARTIST. Gronda, Fredrix, Winsor &
    Newton, Masterpiece, Belle Arti, Claessens, Utrecht, Blick, Art
    Alternatives and the rest stamp the bar or the selvedge of EVERY
    stretched canvas they sell, painted or not. That stamp names who made
    the SUPPORT the artist bought. Never lead a title with it, never put it
    in brand, and never let it become the maker of the work. Record it as
    the support ("stretched canvas, Gronda mill stamp on the stretcher") --
    it dates and places the canvas, which is worth having, and it is not who
    painted it.
  * READ THE BACK, IT IS THE MOST INFORMATIVE SIDE. Transcribe verbatim
    every inscription in pencil, ink, paint or chalk (artists title, date,
    sign, number and price their work on the verso), every gallery,
    exhibition, framer, auction or supplier label, every inventory or lot
    number, any stencil, customs mark or old price. Hand-written on the back
    of a canvas is exactly where a painting's title and artist usually are
    when the front is unsigned, so this is a close look, not a glance.
  * THE FRONT IS THE ITEM AND YOU HAVE NOT SEEN IT. Draft nothing about the
    picture -- not the subject, not the palette, not the medium, not
    "abstract", not "original" and not "print" -- from a photo of the back.
    Say in raw_observations which side each photo shows. If NO photo shows
    the front, say so plainly, keep confidence low, and ask for it in
    missing_info by name: "photograph the FRONT of the painting, the whole
    picture, straight on", "photograph the lower corners of the front close
    up for a signature", "photograph any writing or labels on the back".
  * THE SAME ERROR WEARS A FRAME. The back of a framed piece -- brown paper
    dust cover, hanging wire, turn buttons or points, framer's label -- is
    NOT an empty picture frame, and a mat with nothing visible in the window
    is not an empty mat. An empty frame is a frame photographed from the
    FRONT with nothing in it. Same rule: read the back, ask for the front.
  * THE ONE REAL EXCEPTION is a canvas that is plainly RETAIL STOCK, and it
    announces itself: still in shrink-wrap, a barcode or price sticker, a
    printed size label, a corner protector, a multipack or several identical
    canvases in the frame, photographed face-on with an unmistakably blank
    white primed front. Only then is a blank canvas the item, and it is a
    low-value art supply -- list it as one, by its mill and size, and never
    at the price a painting would fetch.
"""

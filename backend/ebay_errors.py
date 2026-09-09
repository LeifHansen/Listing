"""Turn raw eBay API errors into plain-language fixes the seller can act on.

eBay returns errors like:
  {"errors":[{"errorId":25002,"message":"...No <Item.Country>...",
              "parameters":[{"name":"0","value":"Item.Country"}]}]}

`from_response()` parses that into a list of "issues", each carrying a
human title, a concrete fix, and a `target` telling the UI which field to
highlight (category / price / title / description / specifics / photos /
location / policies / generic).
"""
from __future__ import annotations

import json
import re
from typing import Optional

from .models import SUBTITLE_MAX_CHARS, TITLE_MAX_CHARS


def _parse(text: str) -> list[dict]:
    """Pull eBay's `errors` array out of a response body (JSON or not)."""
    if not text:
        return []
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return [{"message": text[:300]}]
    errs = data.get("errors") if isinstance(data, dict) else None
    return errs if isinstance(errs, list) else []


def _clip(text: str, limit: int = 160) -> str:
    """`text` short enough to sit in a one-line title, ending on a whole word."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


# eBay names the aspect inside the sentence when it sends no parameters:
# "The item specific Unit Quantity is missing", "Missing required item
# specific: Country/Region of Manufacture", "the required item specific
# 'Type'". Pull the name out of any of those, so the editor can ring the
# field instead of listing three examples that are already filled.
_ASPECT_IN_TEXT = re.compile(
    r"""(?:item\s+specifics?|aspect|required\s+attribute)   # what eBay called it
        \s*[:\-]?\s*                                       # optional punctuation
        ["'\u201c\u2018]?                                  # optional quote
        ([A-Z][A-Za-z0-9]*(?:[ /&\-][A-Za-z0-9()][A-Za-z0-9()]*){0,4})
    """,
    re.VERBOSE)

# Words that follow "item specific" without being one, so a sentence like
# "the item specific is missing" never yields "Is".
_NOT_AN_ASPECT = {"is", "was", "are", "were", "must", "missing", "required",
                  "value", "values", "name", "for", "with", "and", "the",
                  "invalid", "not", "cannot", "should"}


# Trailing words the sentence continues with, never the end of an aspect
# name: "The item specific Unit Quantity is missing" must yield "Unit
# Quantity", not "Unit Quantity is missing". Trimmed only from the END, so a
# name that legitimately contains one ("Country/Region OF Manufacture")
# keeps it.
_TRAILING_FILLER = {"is", "was", "are", "were", "be", "been", "must", "missing",
                    "required", "invalid", "not", "cannot", "should", "has",
                    "have", "value", "values", "of", "for", "and", "the", "a",
                    "an", "in", "to", "on", "or", "this", "that", "it"}


def _aspect_from_text(text: str) -> str:
    """The aspect name eBay named in its sentence, or ""."""
    for match in _ASPECT_IN_TEXT.finditer(" ".join((text or "").split())):
        words = match.group(1).strip(" '\u2019\"\u201d").split()
        while words and words[-1].lower().strip(".,;:") in _TRAILING_FILLER:
            words.pop()
        name = " ".join(words).strip(" '\u2019\"\u201d.,;:")
        if not name or name.split()[0].lower() in _NOT_AN_ASPECT:
            continue
        if len(name) <= 40:
            return name
    return ""


# eBay's wording for a fixed-choice aspect refusing a value: the value in
# straight quotes, then the aspect's name up to the full stop. Both halves are
# captured so the seller can be told which box to open and what was in it.
_NOT_AN_OPTION_RE = re.compile(
    r'["“]([^"”]{1,80})["”]\s+is not a valid value for\s+([^.\n]{1,40}?)\s*(?:\.|$)',
    re.IGNORECASE)


def _not_an_option(text: str) -> Optional[tuple[str, str]]:
    """(value, aspect) when `text` is eBay refusing a value that is not on its
    list for an aspect, else None."""
    m = _NOT_AN_OPTION_RE.search(text or "")
    if not m:
        return None
    value, aspect = m.group(1).strip(), m.group(2).strip()
    return (value, aspect) if value and aspect else None


def _looks_like_weight(value: str) -> bool:
    """True for values like '3 oz', '1.5 lb', '70 lbs'."""
    parts = value.strip().lower().split()
    return (len(parts) == 2 and parts[1] in ("oz", "lb", "lbs", "ounces", "pounds")
            and parts[0].replace(".", "", 1).isdigit())


# eBay's own name for a price promotion has changed twice — Markdown Manager,
# then "Promotional sale", then "Discount" on Seller Hub since 2024 — and the
# API sentence still uses whichever the seller's account was set up under. All
# three are the same refusal, so all three are matched here.
#
# These four name the item-level feature and nothing else, which is what makes
# them safe to read on their own (see _SHIPPING_DISCOUNT).
_ITEM_SALE_WORDS = ("markdown", "promotional sale", "promotional price",
                    "promotion")

_SALE_WORDS = _ITEM_SALE_WORDS + (
    "sale event", "part of a sale", "in a sale", "in a discount",
    "price discount", "item discount", "discounted price", "on sale")

# A shipping discount is a different feature wearing the same words —
# combined-postage rules and free-shipping promotions, not a markdown on the
# item — and a refusal about one would otherwise be answered "your item is in
# a sale", sending the seller to a promotion that isn't there. Cut out of the
# sentence before anything else is read, longest phrase first so
# "promotional shipping" goes before the "shipping" and "promotional" inside
# it, and what's left is judged on its own.
_SHIPPING_DISCOUNT = tuple(sorted(
    ("shipping discount", "postage discount", "combined shipping",
     "combined postage", "shipping promotion", "promotional shipping",
     "free shipping promotion"), key=len, reverse=True))

# ...and the half that makes it a REFUSAL rather than a mention. eBay says
# "blocked", "not allowed", "cannot be revised", "remove the item from the
# sale"; a sentence carrying a sale word without one of these is not this
# error.
_SALE_LOCK_WORDS = ("cannot", "can not", "can't", "cannot be revised",
                    "not allowed", "not permitted", "unable", "blocked",
                    "block revision", "restricted", "prevented",
                    "remove the item", "remove it from", "must be removed",
                    "not be revised", "not be changed")


def sale_locked(text: str) -> bool:
    """Is this eBay refusing an edit because the item is in a SALE?

    eBay lets a seller run a markdown/discount with "Keep items in this sale
    and block revisions for price increases" ticked. While that is on, the
    price on a live listing cannot be revised at all — the refusal is about
    the sale, and there is nothing wrong with the price the seller typed.

    The wording is what's matched, not an error code: eBay returns this under
    the same 21916xxx "restricted revise" family it uses for pending Best
    Offers and near-end auctions, so the code alone can't tell them apart —
    the same reason `ebay_trading.specifics_locked` reads the sentence.
    """
    hay = " ".join((text or "").lower().split())
    for phrase in _SHIPPING_DISCOUNT:
        hay = hay.replace(phrase, " ")
    # Both halves, or it isn't this error: a sale NAMED, and a refusal. eBay's
    # text mentions sales and discounts all over the place without refusing
    # anything, and "cannot be revised" on its own is the pending-Best-Offer
    # freeze that the branch below this one answers.
    return (any(w in hay for w in _SALE_WORDS)
            and any(w in hay for w in _SALE_LOCK_WORDS))


def explain(err: dict) -> dict:
    """Map one eBay error to {title, fix, target, ebay_message, error_id}."""
    error_id = str(err.get("errorId", "") or "")
    message = err.get("message", "") or ""
    long_message = err.get("longMessage", "") or ""
    params = err.get("parameters") or []
    param_vals = " ".join(str(p.get("value", "")) for p in params)
    hay = f"{message} {long_message} {param_vals}".lower()

    def has(*words: str) -> bool:
        return any(w in hay for w in words)

    def has_word(*words: str) -> bool:
        """Whole-word match. Short identifiers need it: plain `in` made "ean"
        fire on "means" and "clean", filing unrelated rejections under
        "eBay wants a UPC/EAN"."""
        return any(re.search(rf"\b{re.escape(w)}\b", hay) for w in words)

    issue = {"error_id": error_id, "ebay_message": message or long_message}

    # eBay error 240 first: its wording mentions the title, the description and
    # eBay policy all at once, so every branch below would claim it and send
    # the seller to fix a field that is fine. It is an ACCOUNT-level block far
    # more often than a wording problem — eBay's own guidance is that the real
    # reason arrives in the response's <Message>, which ebay_trading now keeps.
    if error_id == "240" or has("cannot be listed or modified", "improper words"):
        # When eBay attached a real reason (the response's <Message>, carried
        # here as longMessage), that IS the answer — lead with eBay's own
        # words. Only fall back to explaining the code when it said nothing.
        said = long_message.strip()
        generic = ("cannot be listed or modified" in said.lower()
                   or "improper words" in said.lower())
        explained = bool(said) and not generic
        issue.update(
            target="account",
            # A 240 eBay declined to explain is a PLACEHOLDER, not a finding:
            # it says a publish stopped and nothing more. Marking it as one
            # lets ebay_account order a real diagnosis ahead of it, and lets
            # the one-line surfaces prefer anything they have over it.
            placeholder=not explained,
            # eBay's words go in the TITLE, not just the fix. The bulk card,
            # the drafts strip and the publish toast all render the title and
            # nothing else, so a reason left in `fix` is a reason the seller
            # never sees — "eBay won't accept this listing" told them exactly
            # as much as the placeholder it replaced.
            title=(f"eBay’s reason: {_clip(said)}" if explained
                   else "eBay refused this listing and wouldn't say why"),
            fix=(f"eBay's reason: “{said}”" if explained else
                 "eBay sends this code without naming a cause. It is usually "
                 "the account rather than the listing — a seller account that "
                 "hasn't finished registration or payments setup, a listing "
                 "limit, or a verification eBay is waiting on — and only "
                 "sometimes the words in the title or description. Open eBay "
                 "→ My eBay → Selling and clear anything flagged there, then "
                 "publish again. If nothing is flagged, eBay Customer Service "
                 "can say what the hold is."))
        return issue

    # Codes whose wording would otherwise be captured by a text branch below.
    # eBay's error IDs are stable; the sentences around them are not, and both
    # of these read as something they aren't: the selling-limit message says
    # "exceed the amount you can list", and "amount" belongs to the price
    # branch, so a seller at their limit was told to fix a price that was fine.
    if error_id == "21919188":
        issue.update(target="generic",
                     title="Your eBay selling limit is reached",
                     fix="This listing would put you over the amount your "
                         "account may have listed at once. Nothing is wrong "
                         "with the listing. Ask eBay to raise the limit from "
                         "Seller Hub → Overview → Monthly limits, or publish "
                         "this once something else sells or ends.")
        return issue
    if error_id == "21919144":
        issue.update(target="generic",
                     title="eBay’s API rate limit was hit",
                     fix="eBay caps how quickly listings may be added or "
                         "revised. Nothing is wrong with this listing — wait "
                         "a moment and publish again.")
        return issue

    # A listing in an eBay sale, whose price eBay will not let this app touch.
    # Checked HERE, ahead of every text branch, because the sentence eBay
    # sends satisfies two of them and both answer it wrongly: it says "price",
    # so the price branch below called a $39.99 price "missing or invalid" and
    # the editor ringed the field red; and it says "cannot be revised", so the
    # frozen-listing branch blamed a pending Best Offer that did not exist.
    #
    # Neither names the sale, which is the only fact the seller can act on —
    # the price is fine, the listing is fine, and the thing to change is the
    # discount in Seller Hub. So this is filed under "generic": there is no
    # field to open, and pointing a "Fix this" button at the price is the same
    # wrong claim in button form.
    if sale_locked(f"{message} {long_message} {param_vals}"):
        said = _clip(long_message or message)
        issue.update(
            target="generic",
            title="eBay won’t change the price while this item is in a sale",
            fix=((f"eBay's reason: “{said}” " if said else "")
                 + "Nothing is wrong with your price — this listing is in an "
                   "eBay sale (Marketing → Promotions, called Discounts or "
                   "Markdown Manager depending on the account), and that sale "
                   "is set to block price revisions. Take this item out of the "
                   "sale, or untick “Keep items in this sale and block "
                   "revisions for price increases”, then publish again. Your "
                   "new price is saved here either way. Note that eBay drops "
                   "an item from a sale as soon as its price is revised, so "
                   "the discount ends when this goes through."))
        return issue

    # "Not entitled" is a PERMISSION on the seller's eBay account, and the word
    # that carries it — "entitled" — contains "title". The title branch below
    # tested for that with a plain substring `in`, so every one of these
    # rejections landed there and came back as "There's a problem with the
    # title. Shorten or fix the title (max 80 characters)."
    #
    # That is the worst possible answer to this error. It names a field that is
    # fine, gives advice that cannot be followed (a 56-character title is
    # already under 80), repeats on every listing the account has — because the
    # block is on the account, not on any listing — and it hides eBay's own
    # sentence, which says what the missing permission actually is. A seller
    # can rewrite the title all day and never get past it.
    #
    # It is filed under "account" so the editor stops ringing a field over it:
    # fixTargetFor() deliberately refuses to jump to an account target.
    if has("not entitled", "n't entitled", "entitled to"):
        said = _clip(long_message or message)
        # The one entitlement this app can do something about. Business
        # policies are an eBay program an account has to be opted into
        # (SELLING_POLICY_MANAGEMENT); until it is, every policy id the
        # publish sends is refused, and Settings can switch it on.
        policies = has("business polic", "selling polic", "policy management")
        issue.update(
            target="account",
            title=("Your eBay account isn’t set up for business policies"
                   if policies else
                   (f"eBay hasn’t granted this account: {said}" if said
                    else "Your eBay account isn’t allowed to do this yet")),
            fix=(("eBay refuses the listing because this account isn't opted "
                  "into its business-policies program, which is what payment, "
                  "shipping and return policies are attached with. Open "
                  "Settings → eBay and turn business policies on (eBay can "
                  "take up to 24 hours to enable it), then publish again. "
                  "Nothing is wrong with your title or your listing.")
                 if policies else
                 ((f"eBay's words: “{said}”. " if said else "")
                  + "This is a permission on your eBay account, not a problem "
                    "with this listing — the same refusal will come back on "
                    "every listing until it's cleared. Open eBay → My eBay → "
                    "Selling and clear anything flagged there; if nothing is, "
                    "eBay Customer Service can say which permission is "
                    "missing. Your title is fine.")))
    elif has("item.country", "merchantlocation", "merchant location",
           "inventory location", "ship-from", "ship from", "location key"):
        issue.update(target="location",
                     title="eBay needs a valid ship-from location",
                     fix="Open Listing settings and add (or re-save) your ship-from ZIP.")
    elif has("condition"):
        # Check condition BEFORE category: eBay's 25021 message mentions both
        # ("condition id is invalid for the selected primary category").
        issue.update(target="condition",
                     title="This condition isn’t valid for the selected category",
                     fix="Pick a condition from the dropdown — it now lists only the "
                         "conditions eBay allows for this category.")
    elif has("category") and not has("item specific", "aspect", "required attribute"):
        # ...but only when the category is what eBay is actually complaining
        # about. "The aspect Unit Type is required for this category" says
        # "category" while naming an item specific, and this branch claimed it
        # — sending the seller to re-pick a category that was correct, over an
        # aspect they were never told about. The specifics branch below is the
        # more specific reading, so it gets first refusal on anything that
        # names one.
        issue.update(target="category",
                     title="This item needs a valid eBay category",
                     fix="Use “Suggest eBay categories” and pick the closest match.")
    elif has("over the weight limit", "weight limit for service"):
        # 25007: the shipping policy includes a service with a max weight
        # (e.g. eBay Standard Envelope, 3 oz) that this package exceeds. The
        # weight itself is usually fine — the policy is the problem.
        limit = next((str(p.get("value")) for p in params
                      if _looks_like_weight(str(p.get("value", "")))), "")
        service = next((str(p.get("value")) for p in params
                        if " " in str(p.get("value", ""))
                        and not str(p.get("value", "")).startswith("err:")
                        and not _looks_like_weight(str(p.get("value", "")))), "a service in it")
        issue.update(
            target="policies",
            title="This package is too heavy for the shipping policy",
            fix=(f"The selected shipping policy includes {service}, which maxes out at "
                 f"{limit or 'a lower weight'}. Either switch this listing to a shipping "
                 "policy that supports heavier packages (Settings → Listing defaults, or "
                 "edit the policy on eBay), or lower the package weight to fit."))
    elif has("must be greater than 0", "number after the decimal"):
        # A NUMBER-typed item specific holding text or zero — "Fabric weight
        # must be greater than 0. Enter up to 1 number after the decimal."
        # NOT the shipping weight (that lives on the package), even though the
        # word "weight" appears; the old mapping sent sellers to re-enter a
        # package weight that was already fine.
        aspect = next((v for v in (str(p.get("value", "")).strip() for p in params)
                       if v and v[:1].isupper() and len(v) <= 40
                       and not v.endswith((".", "!")) and len(v.split()) <= 5), "")
        issue.update(
            target="specifics",
            # The aspect by NAME, not only inside the sentence: the editor
            # rings the field eBay named instead of leaving the seller to
            # find it among forty of them. See SpecificsCard.
            fields=[aspect] if aspect else [],
            title=(f"“{aspect}” needs a plain number" if aspect
                   else "An item specific needs a plain number"),
            fix="Under Item specifics, make it just a number (one decimal at "
                "most, e.g. “14”) — or clear it if it doesn't apply. Your "
                "shipping weight is a separate field and may already be fine.")
    elif has("weight", "package", "shipping package", "dimensions"):
        issue.update(target="weight",
                     title="eBay needs a valid shipping weight",
                     fix="Enter the package weight (lb / oz) in the listing, then publish again.")
    elif has("brandmpn", "brand/mpn"):
        issue.update(target="specifics",
                     fields=["Brand", "MPN"],
                     title="eBay needs Brand and MPN for this category",
                     fix=("Set a Brand (use “Unbranded” if there isn’t one) and add an "
                          "item specific “MPN” — “Does Not Apply” works for items "
                          "without a part number."))
    elif (has("product identifier", "does not apply")
          or has_word("upc", "ean", "isbn", "gtin")):
        issue.update(target="specifics",
                     fields=["UPC"],
                     title="eBay wants a product identifier (UPC/EAN)",
                     fix="Add an item specific “UPC” set to “Does not apply” for vintage/handmade items.")
    elif has("cannot be changed", "can not be changed", "cannot be revised"):
        # eBay refusing to change something RIGHT NOW, which is a different
        # thing from anything being wrong with it — and the sentence it uses
        # says "item specifics", so the branch below claimed it and answered
        # "Missing required item specific" on a listing whose specifics were
        # complete. The seller was sent hunting for an empty field that did
        # not exist, every time they saved, for as long as the offer stood.
        #
        # eBay's own words are the whole answer here, so they lead. Filed
        # under "generic": there is no field to open and fix, and a "Fix this"
        # button pointing at the specifics grid is the same wrong claim in
        # button form.
        said = _clip(long_message or message)
        offer = has("best offer", "auction", "bid")
        issue.update(
            target="generic",
            title=("eBay has this listing frozen for now"
                   if offer else f"eBay won’t change that on a live listing: {said}"),
            fix=((f"eBay's reason: “{said}” " if said else "")
                 + ("Nothing is missing and nothing is wrong with the listing "
                    "— eBay locks parts of a listing while a Best Offer is "
                    "waiting on it, or an auction has a bid or ends within 12 "
                    "hours. Your edit is saved here and goes over "
                    "automatically once that clears. Accepting or declining "
                    "the offer lifts it immediately."
                    if offer else
                    "This part of a listing can't be changed once it is live. "
                    "The edit is saved here; end the listing and relist it if "
                    "it has to reach eBay.")))
    elif _not_an_option(f"{message} {long_message}"):
        # '"W" is not a valid value for Size. Select a value from the
        # available options.' — eBay refusing a value that is not on its list
        # for a fixed-choice aspect. The sentence names neither "item
        # specific" nor "aspect", so it fell through to the generic branch:
        # the seller was told "eBay rejected the listing" over eBay's raw
        # words, with no field to open and nothing to pick from. It IS an
        # item-specifics refusal, and the aspect is right there in the text.
        value, aspect = _not_an_option(f"{message} {long_message}")
        issue.update(
            target="specifics",
            fields=[aspect],
            title=f"“{value}” isn’t one of eBay’s options for {aspect}",
            fix=(f"Pick {aspect} from eBay’s list under Item specifics — "
                 f"for this one eBay only accepts a value from its own list, "
                 f"so “{value}” has to become the closest option on it."))
    elif has("item specific", "aspect", "required attribute", "missing value"):
        # The aspect name rides along in the parameters next to full-sentence
        # copies of the message ("The item specific Item Height is missing.").
        # Pick the value that looks like a NAME, not a sentence, so the title
        # says "Missing required item specific: Item Height" instead of
        # echoing the whole error text.
        aspect = next((v for v in (str(p.get("value", "")).strip() for p in params)
                       if v and v[:1].isupper() and len(v) <= 40
                       and not v.endswith((".", "!")) and len(v.split()) <= 5), "")
        # ...and when it does not, read it out of the sentence. eBay does not
        # always send parameters — a REVISE of a live listing often carries the
        # name in the message alone — and the fallback below was pure
        # boilerplate ("e.g. Brand, Type, Size") on a listing whose Brand, Type
        # and Size were all filled in. That is a seller staring at a card
        # reading "Required to publish 5/5, nothing here is blocking" beside a
        # refusal naming a sixth aspect they were never shown.
        if not aspect:
            aspect = _aspect_from_text(f"{message} {long_message}")
        dimension = aspect.lower().removeprefix("item ").strip() in (
            "height", "length", "width", "depth", "diameter", "weight")
        # Nothing named, either way: eBay's own sentence is the most
        # informative thing we hold, so say it rather than guessing at three
        # aspects that are probably already filled. Same rule as the 240
        # branch above — the seller's next move depends on eBay's words, and a
        # reason we do not render is a reason they never see.
        said = _clip(" ".join((long_message or message or "").split()))
        issue.update(
            target="specifics",
            fields=[aspect] if aspect else [],
            title=("Missing required item specific"
                   + (f": {aspect}" if aspect
                      else (f" — eBay’s reason: {said}" if said else ""))),
            fix=((f"Add “{aspect}” under Item specifics with a number and unit "
                  f"(e.g. “3 in”). Note: the shipping Package size fields don’t "
                  f"count — eBay wants it as an item specific.")
                 if aspect and dimension else
                 (f"Fill in “{aspect}” under Item specifics." if aspect else
                  ((f"eBay's words: “{said}”. Add that item specific under "
                    "Item specifics. eBay adds new required specifics to a "
                    "category from time to time, so one that published before "
                    "can be refused now — the field may not be in this "
                    "listing's list yet.")
                   if said else
                   "Add the required item specifics (e.g. Brand, Type, Size) "
                   "under Item specifics."))))
    elif has("return policy", "returnpolicy"):
        issue.update(target="policies",
                     title="A return policy is required",
                     fix="Choose a return policy in Listing settings.")
    elif has("payment policy", "paymentpolicy"):
        issue.update(target="policies",
                     title="A payment policy is required",
                     fix="Choose a payment policy in Listing settings.")
    elif has("fulfillment", "shipping policy", "shipping service"):
        issue.update(target="policies",
                     title="A shipping policy is required",
                     fix="Choose a shipping policy in Listing settings.")
    elif has("price", "pricingsummary", "amount"):
        # "The price is missing or invalid" is a CLAIM about what the seller
        # typed, and this branch used to make it on the strength of the word
        # "price" appearing anywhere in eBay's sentence. Every refusal that
        # merely mentions a price — a sale that blocks revisions, a price
        # eBay finds too high for the category, a currency it won't take —
        # came back as "missing or invalid" over a filled-in field, with
        # "Set a price greater than $0" under a price that already was.
        #
        # So the claim is made only when eBay actually made it. Otherwise
        # eBay's own sentence is the most informative thing we hold, and it
        # goes in the title — the toast and the bulk cards render nothing
        # else, so a reason left in `fix` is a reason the seller never sees.
        said = _clip(long_message or message)
        missing = has("missing", "invalid", "not valid", "required",
                      "must be greater", "greater than 0", "greater than zero",
                      "cannot be zero", "is empty", "no price", "blank")
        issue.update(
            target="price",
            title=("The price is missing or invalid" if missing
                   else (f"eBay wouldn’t accept the price: {said}" if said
                         else "eBay wouldn’t accept the price")),
            fix=("Set a price greater than $0." if missing else
                 ((f"eBay's words: “{said}”. " if said else "")
                  + "That is eBay objecting to the price it was sent, not a "
                    "check made here — set the price to what eBay asked for "
                    "and publish again.")))
    elif has_word("subtitle", "subtitles") or has("sub-title"):
        # "subtitle" contains "title" too, so a rejection over the optional
        # subtitle was answered with the TITLE's 80-character limit — advice
        # for a field the seller hadn't touched. It shares the Title card, so
        # it keeps that target; what changes is that the answer names the
        # field eBay named, and its own (shorter) limit.
        said = _clip(long_message or message)
        issue.update(
            target="title",
            title=(f"eBay wouldn’t accept the subtitle: {said}" if said
                   else "eBay wouldn’t accept the subtitle"),
            fix=("The subtitle is the optional paid line under your title, up "
                 f"to {SUBTITLE_MAX_CHARS} characters. Shorten it — or clear "
                 "it, since it's optional — and publish again. Your title "
                 "itself is not what eBay refused."))
    elif has_word("title", "titles"):
        # Whole-word, because "title" is a substring of "subtitle",
        # "untitled" and "entitled", and all three used to be answered as
        # title problems. See the entitlement branch at the top.
        said = _clip(long_message or message)
        # Only claim it's the LENGTH when eBay said so. The old copy said it
        # every time, which is how a seller with a 56-character title was told
        # to shorten it to 80 — twice, then a third time, with no other
        # information anywhere on the screen and eBay's actual sentence thrown
        # away here.
        too_long = has("too long", "exceeds the maximum", "maximum length",
                       "max length", "characters or less", "80 characters")
        issue.update(
            target="title",
            title=(f"eBay wouldn’t accept the title: {said}" if said
                   else "eBay wouldn’t accept the title"),
            fix=(f"Shorten the title to {TITLE_MAX_CHARS} characters or fewer, "
                 "then publish again." if too_long else
                 ((f"eBay's words: “{said}”. " if said else "")
                  + "Edit the title to match what eBay asked for. Titles are "
                    f"limited to {TITLE_MAX_CHARS} characters and can't carry "
                    "HTML, or wording eBay's listing policies don't allow.")))
    elif has("description"):
        issue.update(target="description",
                     title="The description needs work",
                     fix="Add a fuller item description.")
    elif has("image", "picture", "photo", "epsimageurl"):
        issue.update(target="photos",
                     title="eBay couldn’t use the photos",
                     fix="Go back to images and re-upload clear photos.")
    elif has("offer entity already exists"):
        issue.update(target="generic",
                     title="This item already has an eBay offer",
                     fix="Just press Publish Live again — we’ll update the existing offer.")
    # The two "limit" rejections that stop a publish for reasons the listing
    # itself can't fix. They used to fall through to the generic branch, which
    # reads as "eBay rejected the listing" and sends the seller hunting through
    # fields that were never the problem.
    elif has("call limit", "exceeded the number of calls", "maximum number of calls",
             "application-level", "too many requests", "throttl"):
        issue.update(target="generic",
                     title="eBay’s API limit for the app was reached",
                     fix="Nothing is wrong with this listing. eBay caps how "
                         "many API calls the app may make per day, and today’s "
                         "allowance is spent — it resets at midnight Pacific. "
                         "Try publishing again after the reset.")
    elif has("listing limit", "selling limit", "monthly limit",
             "exceeded your limit", "sell more items"):
        issue.update(target="generic",
                     title="Your eBay selling limit is reached",
                     fix="eBay caps how many items (or how much value) your "
                         "account may list per month. Ask eBay to raise the "
                         "limit from Seller Hub → Overview → Monthly limits, "
                         "or publish this once something else sells or ends.")
    else:
        issue.update(target="generic",
                     title="eBay rejected the listing",
                     fix=(message or long_message or "See the details below."))
    return issue


def from_response(text: str) -> list[dict]:
    """Parse an eBay error body into a de-duplicated list of issues."""
    issues, seen = [], set()
    for err in _parse(text):
        it = explain(err)
        key = (it["target"], it["title"])
        if key not in seen:
            seen.add(key)
            issues.append(it)
    if not issues:
        issues.append({"target": "generic", "title": "eBay rejected the listing",
                       "fix": "See the details below.", "error_id": "", "ebay_message": ""})
    return issues


def from_trading_error(exc: Exception) -> list[dict]:
    """Issues for a Trading API failure, using everything the error carries.

    `from_response` only ever sees the headline string. A TradingError also
    knows eBay's ErrorCode and the response-level <Message> that explains a
    catch-all rejection, and both change what the seller should be told — so
    they're fed to `explain` directly rather than being thrown away.
    """
    code = str(getattr(exc, "code", "") or "")
    detail = str(getattr(exc, "detail", "") or "")
    # `said` — eBay's response-level <Message> alone — is what `explain` may
    # quote as eBay's reason. `detail` is NOT interchangeable with it: it also
    # carries warnings and trailing errors, so passing it here told a seller
    # "eBay's reason: <a warning about something else>" on a rejection eBay
    # had in fact declined to explain, and hid the real diagnosis behind it.
    said = str(getattr(exc, "said", "") or "")
    if getattr(exc, "unreachable", False):
        # The request never left, so eBay neither acted nor refused. The
        # generic branch would title this "eBay rejected the listing", which
        # is what the short surfaces render — and it sends the seller hunting
        # through fields when the problem is the network. Unlike the unknown
        # case below, this one CAN say nothing was sent.
        return [{"error_id": "", "ebay_message": str(exc),
                 "target": "generic",
                 "title": "Couldn't reach eBay",
                 "fix": "We couldn't get a connection to eBay, so nothing was "
                        "sent. Try again in a moment."}]
    if getattr(exc, "outcome_unknown", False):
        # Not a rejection, and it must not be titled as one. The fix panel and
        # the bulk cards render the TITLE, and the short surfaces render only
        # the title -- so the generic branch's "eBay rejected the listing" put
        # the one claim we cannot make in the largest text on the screen,
        # directly above a body saying the opposite. A seller who reads
        # "rejected" fixes something and publishes again, which is how the
        # duplicate live listing happens.
        # The instruction is written HERE rather than taken from str(exc), so
        # it cannot go missing depending on how a caller happened to word the
        # exception. "Check before retrying" is the entire actionable content
        # of this issue -- everything else is context.
        return [{"error_id": "", "ebay_message": str(exc),
                 "target": "generic",
                 "title": "We could not confirm what eBay did",
                 "fix": "The request reached eBay and the answer didn't come "
                        "back, so we can't tell whether it went through. "
                        "Check this item in your eBay listings before trying "
                        "again — retrying blind could publish it twice."}]
    issues = [explain({"errorId": code, "message": str(exc),
                       "longMessage": said})]
    if detail and detail not in (issues[0].get("ebay_message") or ""):
        # Keep eBay's own words available to the UI even when the branch above
        # replaced them with a plainer explanation.
        issues[0]["ebay_detail"] = detail
    return issues


def headline(issues: list[dict], step: str, status: Optional[int] = None) -> str:
    """A one-line summary for the top of the fix panel."""
    n = len(issues)
    where = {"createOffer": "creating the offer",
             "updateOffer": "updating the offer",
             "publishOffer": "publishing",
             "createOrReplaceInventoryItem": "saving the item"}.get(step, step or "publishing")
    return (f"eBay stopped {where} — {n} thing{'s' if n != 1 else ''} to fix"
            + (f" (error {status})" if status else "") + ":")

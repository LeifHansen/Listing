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


def _looks_like_weight(value: str) -> bool:
    """True for values like '3 oz', '1.5 lb', '70 lbs'."""
    parts = value.strip().lower().split()
    return (len(parts) == 2 and parts[1] in ("oz", "lb", "lbs", "ounces", "pounds")
            and parts[0].replace(".", "", 1).isdigit())


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

    if has("item.country", "merchantlocation", "merchant location",
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
    elif has("category"):
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
                     title="eBay needs Brand and MPN for this category",
                     fix=("Set a Brand (use “Unbranded” if there isn’t one) and add an "
                          "item specific “MPN” — “Does Not Apply” works for items "
                          "without a part number."))
    elif (has("product identifier", "does not apply")
          or has_word("upc", "ean", "isbn", "gtin")):
        issue.update(target="specifics",
                     title="eBay wants a product identifier (UPC/EAN)",
                     fix="Add an item specific “UPC” set to “Does not apply” for vintage/handmade items.")
    elif has("item specific", "aspect", "required attribute", "missing value"):
        # The aspect name rides along in the parameters next to full-sentence
        # copies of the message ("The item specific Item Height is missing.").
        # Pick the value that looks like a NAME, not a sentence, so the title
        # says "Missing required item specific: Item Height" instead of
        # echoing the whole error text.
        aspect = next((v for v in (str(p.get("value", "")).strip() for p in params)
                       if v and v[:1].isupper() and len(v) <= 40
                       and not v.endswith((".", "!")) and len(v.split()) <= 5), "")
        dimension = aspect.lower().removeprefix("item ").strip() in (
            "height", "length", "width", "depth", "diameter", "weight")
        issue.update(
            target="specifics",
            title=("Missing required item specific" + (f": {aspect}" if aspect else "")),
            fix=((f"Add “{aspect}” under Item specifics with a number and unit "
                  f"(e.g. “3 in”). Note: the shipping Package size fields don’t "
                  f"count — eBay wants it as an item specific.")
                 if aspect and dimension else
                 (f"Fill in “{aspect}” under Item specifics." if aspect else
                  "Add the required item specifics (e.g. Brand, Type, Size) "
                  "under Item specifics.")))
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
        issue.update(target="price",
                     title="The price is missing or invalid",
                     fix="Set a price greater than $0.")
    elif has("title"):
        issue.update(target="title",
                     title="There’s a problem with the title",
                     fix="Shorten or fix the title (max 80 characters).")
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

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

    issue = {"error_id": error_id, "ebay_message": message or long_message}

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
    elif has("upc", "ean", "isbn", "gtin", "product identifier", "does not apply"):
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


def headline(issues: list[dict], step: str, status: Optional[int] = None) -> str:
    """A one-line summary for the top of the fix panel."""
    n = len(issues)
    where = {"createOffer": "creating the offer",
             "updateOffer": "updating the offer",
             "publishOffer": "publishing",
             "createOrReplaceInventoryItem": "saving the item"}.get(step, step or "publishing")
    return (f"eBay stopped {where} — {n} thing{'s' if n != 1 else ''} to fix"
            + (f" (error {status})" if status else "") + ":")

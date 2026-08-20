"""Deciding what survives when duplicate drafts are consolidated into one.

A bulk batch that splits one item's photos across two drafts leaves two
half-right listings: one got the better title, the other got the price and
the brand. The old merge combined the photos and kept whatever the FIRST
selected draft happened to say — every other draft's writing went in the bin
with its record, silently.

So a merge is a decision the seller makes now, and this module is the half of
that decision with nothing to do with HTTP or storage:

  * `review()` reports, for a chosen master and the drafts being consolidated
    into it, every field the drafts disagree about — each with the values on
    offer and which draft each came from — plus the fields the master leaves
    blank that a consolidated draft can fill in.
  * `resolve()` applies the seller's picks (and those blank-fills) to the
    master's data, and reports what came from where.

Both walk the same fields in the same order, so what the review screen shows
is exactly what the merge writes.

Two rules shape the rest:

  * A blank is not an entry. The master having no brand while a duplicate has
    one is not a conflict worth a question — it is the missing half of the
    listing, and it is filled in.
  * The master wins ties. Every default is the master's own value; a
    consolidated draft's value lands only where the seller picks it, or where
    the master had nothing there at all.
"""
from __future__ import annotations

from dataclasses import dataclass

# eBay condition codes and listing formats, spelled the way the editor spells
# them. Anything not named here falls back to a de-shouted version of the raw
# value, which reads fine for codes this doesn't know.
CONDITION_LABELS = {
    "NEW": "New",
    "NEW_OTHER": "New (other)",
    "NEW_WITH_DEFECTS": "New with defects",
    "CERTIFIED_REFURBISHED": "Certified refurbished",
    "SELLER_REFURBISHED": "Seller refurbished",
    "USED_EXCELLENT": "Used — excellent",
    "USED_VERY_GOOD": "Used — very good",
    "USED_GOOD": "Used — good",
    "USED_ACCEPTABLE": "Used — acceptable",
    "FOR_PARTS_OR_NOT_WORKING": "For parts / not working",
}
FORMAT_LABELS = {
    "FIXED_PRICE": "Buy It Now",
    "AUCTION": "Auction",
    "AUCTION_BIN": "Auction + Buy It Now",
}

# Item specifics are fields too ("Size: 10" vs "Size: 10.5" is exactly the
# disagreement this screen exists for), but they are named by the seller and
# the AI rather than by the model, so they get their own key space.
SPECIFIC_PREFIX = "specific:"

# A description can run to thousands of characters. The seller is choosing
# between values, not reading them in full here, so the OPTION is clipped —
# the value that gets written is always read back off the record itself.
MAX_DISPLAY = 600


@dataclass(frozen=True)
class Spec:
    """One reviewable field: what to call it, which Listing keys carry it,
    and how its values are read, compared and shown."""

    key: str
    label: str
    names: tuple[str, ...]
    kind: str = "text"


# The fields worth asking about, in the order the editor shows them. Left out
# on purpose: photos (a merge always combines every draft's, that being the
# point), and the plumbing — ids, source, marketplace state — which belongs to
# the master's record and would be nonsense taken from a draft about to be
# deleted.
SPECS: tuple[Spec, ...] = (
    Spec("title", "Title", ("title",)),
    Spec("subtitle", "Subtitle", ("subtitle",)),
    Spec("brand", "Brand", ("brand",)),
    Spec("condition", "Condition", ("condition",), "choice"),
    Spec("condition_description", "Condition notes",
         ("condition_description",), "long"),
    Spec("category", "eBay category", ("category_id", "category_suggestion"),
         "category"),
    Spec("description", "Description", ("description",), "long"),
    Spec("price", "Price", ("price",), "money"),
    Spec("purchase_price", "What you paid", ("purchase_price",), "money"),
    Spec("quantity", "Quantity", ("quantity",), "number"),
    Spec("listing_format", "Selling format", ("listing_format",), "choice"),
    Spec("auction_start_price", "Starting bid", ("auction_start_price",), "money"),
    Spec("auction_duration", "Auction length", ("auction_duration",), "choice"),
    Spec("package_weight", "Package weight",
         ("package_weight_lb", "package_weight_oz"), "weight"),
    Spec("package_size", "Package size",
         ("package_length_in", "package_width_in", "package_height_in"), "size"),
)
SPEC_BY_KEY = {s.key: s for s in SPECS}


@dataclass
class Field:
    """One field after every draft has been read: the distinct values on
    offer (the master's first, when it has one), which one wins by default,
    and why this field is being reported at all."""

    key: str
    label: str
    kind: str
    options: list[dict]
    default: str
    conflict: bool   # two or more drafts have DIFFERENT entries here
    filled: bool     # the master had nothing here; one draft can fill it


# ------------------------------------------------------------ reading values

def _text(value) -> str:
    """Collapsed whitespace, so two drafts that differ only in line breaks
    are not reported as disagreeing."""
    return " ".join(str(value or "").split())


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clip(text: str) -> str:
    return text if len(text) <= MAX_DISPLAY else text[:MAX_DISPLAY - 1].rstrip() + "…"


def _money(amount: float, currency) -> str:
    code = (_text(currency) or "USD").upper()
    return f"${amount:,.2f}" if code == "USD" else f"{amount:,.2f} {code}"


def _plain(raw: str) -> str:
    return raw.replace("_", " ").capitalize()


def _choice_label(key: str, raw: str) -> str:
    up = raw.upper()
    if key == "condition":
        return CONDITION_LABELS.get(up, _plain(up))
    if key == "listing_format":
        return FORMAT_LABELS.get(up, _plain(up))
    if key == "auction_duration":
        days = up.replace("DAYS_", "")
        return f"{days} days" if days.isdigit() else _plain(up)
    return _plain(up)


def _read(spec: Spec, data: dict) -> tuple[str, str]:
    """(comparison key, display text) for this field on one draft.

    The comparison key is "" for a blank — nothing to ask about and nothing
    to overwrite. Zero counts as blank for money and measurements: a $0 price
    or a 0 lb package is an empty box, not a seller's answer.
    """
    if spec.kind == "money":
        amount = _num(data.get(spec.names[0]))
        if amount <= 0:
            return "", ""
        return f"{amount:.2f}", _money(amount, data.get("currency"))

    if spec.kind == "number":
        count = _num(data.get(spec.names[0]))
        if count <= 0:
            return "", ""
        return f"{count:g}", f"{count:g}"

    if spec.kind == "weight":
        lb, oz = _num(data.get(spec.names[0])), _num(data.get(spec.names[1]))
        if lb <= 0 and oz <= 0:
            return "", ""
        shown = " ".join(p for p in (f"{lb:g} lb" if lb else "",
                                     f"{oz:g} oz" if oz else "") if p)
        return f"{lb:g}/{oz:g}", shown

    if spec.kind == "size":
        dims = [_num(data.get(name)) for name in spec.names]
        # Dimensions only mean anything as a complete set — eBay's calculated
        # shipping wants all three or none.
        if not all(d > 0 for d in dims):
            return "", ""
        return ("x".join(f"{d:g}" for d in dims),
                " × ".join(f"{d:g}" for d in dims) + " in")

    if spec.kind == "category":
        cid, name = _text(data.get("category_id")), _text(data.get("category_suggestion"))
        if not cid and not name:
            return "", ""
        # Compare on the id when there is one: two drafts pointing at the same
        # eBay category with differently-worded suggestions do not disagree.
        return (cid or name.casefold(),
                f"{name} ({cid})" if cid and name else (name or f"Category {cid}"))

    if spec.kind == "choice":
        raw = _text(data.get(spec.names[0]))
        return ("", "") if not raw else (raw.upper(), _choice_label(spec.key, raw))

    raw = _text(data.get(spec.names[0]))
    return ("", "") if not raw else (raw.casefold(), _clip(raw))


def _specifics(data: dict) -> dict[str, dict]:
    """{folded name: {name, value, confidence}} — first entry of a repeated
    name wins, the same way the editor renders them."""
    out: dict[str, dict] = {}
    for entry in (data.get("item_specifics") or []):
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("name"))
        folded = name.casefold()
        if not folded or folded in out:
            continue
        out[folded] = {"name": name, "value": _text(entry.get("value")),
                       "confidence": entry.get("confidence") or ""}
    return out


# ------------------------------------------------------------- the field walk

def _field(key: str, label: str, kind: str,
           reads: list[tuple[str, tuple[str, str]]], master_id: str):
    """One field's verdict, or None when there is nothing to report: every
    draft agrees, or only the master has anything to say."""
    options: list[dict] = []
    seen: set[str] = set()
    for listing_id, (compare, display) in reads:
        if not compare or compare in seen:
            continue
        seen.add(compare)
        options.append({"listing_id": listing_id, "display": display})
    if not options:
        return None
    # The master is read first, so its value — when it has one — is options[0]
    # and therefore the default.
    default = options[0]["listing_id"]
    conflict = len(options) > 1
    if not conflict and default == master_id:
        return None
    return Field(key=key, label=label, kind=kind, options=options,
                 default=default, conflict=conflict,
                 filled=not conflict and default != master_id)


def _fields(participants: list[tuple[str, dict]]) -> list[Field]:
    """Every field worth reporting across `participants`, the master first."""
    if not participants:
        return []
    master_id = participants[0][0]
    found: list[Field] = []
    for spec in SPECS:
        verdict = _field(spec.key, spec.label, spec.kind,
                         [(lid, _read(spec, data)) for lid, data in participants],
                         master_id)
        if verdict:
            found.append(verdict)

    # Item specifics: the union of every draft's names, in the order they are
    # first seen, so a specific only one duplicate filled in is not lost.
    named = [(lid, _specifics(data)) for lid, data in participants]
    labels: dict[str, str] = {}
    for _, specifics in named:
        for folded, entry in specifics.items():
            labels.setdefault(folded, entry["name"])
    for folded, label in labels.items():
        reads = []
        for listing_id, specifics in named:
            value = (specifics.get(folded) or {}).get("value") or ""
            reads.append((listing_id, (value.casefold(), _clip(value)) if value else ("", "")))
        verdict = _field(SPECIFIC_PREFIX + folded, label, "specific", reads, master_id)
        if verdict:
            found.append(verdict)
    return found


# ----------------------------------------------------------------- the verbs

def review(participants: list[tuple[str, dict]]) -> dict:
    """What the merge would do to the master's fields, for the seller to
    approve or redirect. `participants` is [(listing_id, listing data)] with
    the chosen master FIRST and the drafts to consolidate after it, in the
    order their photos will be appended."""
    conflicts, auto_filled = [], []
    for found in _fields(participants):
        if found.conflict:
            conflicts.append({"key": found.key, "label": found.label,
                              "kind": found.kind, "options": found.options,
                              "default": found.default})
        else:
            option = found.options[0]
            auto_filled.append({"key": found.key, "label": found.label,
                                "kind": found.kind,
                                "listing_id": option["listing_id"],
                                "display": option["display"]})
    return {"conflicts": conflicts, "auto_filled": auto_filled}


def resolve(participants: list[tuple[str, dict]],
            choices: dict | None = None) -> tuple[dict, list[dict]]:
    """The master's listing data with the seller's picks written into it, plus
    a note of every field that came from a draft being consolidated.

    `choices` maps a field key from `review()` to the listing id whose value
    wins. A key nobody offered, or a draft that has nothing to offer for that
    field, falls back to the default — a stale review screen can misinform the
    seller, but it must never write a value no draft ever held.
    """
    if not participants:
        return {}, []
    master_id, master = participants[0]
    merged = dict(master)
    merged["item_specifics"] = [dict(s) for s in (master.get("item_specifics") or [])
                                if isinstance(s, dict)]
    by_id = {listing_id: data for listing_id, data in participants}
    picks = choices if isinstance(choices, dict) else {}

    applied: list[dict] = []
    for found in _fields(participants):
        asked = str(picks.get(found.key) or "").strip()
        offered = {o["listing_id"]: o["display"] for o in found.options}
        winner = asked if asked in offered else found.default
        if winner == master_id:
            continue
        _write(found, merged, by_id[winner])
        applied.append({"key": found.key, "label": found.label, "from": winner,
                        "display": offered[winner], "filled": found.filled})
    return merged, applied


def _write(found: Field, merged: dict, source: dict) -> None:
    """Copy one field's value out of `source` and onto the merged data."""
    if found.key.startswith(SPECIFIC_PREFIX):
        folded = found.key[len(SPECIFIC_PREFIX):]
        entry = _specifics(source).get(folded)
        if not entry:
            return
        for existing in merged["item_specifics"]:
            if _text(existing.get("name")).casefold() == folded:
                existing["value"] = entry["value"]
                existing["confidence"] = entry["confidence"]
                return
        merged["item_specifics"].append(dict(entry))
        return
    spec = SPEC_BY_KEY.get(found.key)
    if not spec:
        return
    # Every key the field carries moves together: a category id without its
    # suggestion, or pounds without ounces, would be a value no draft held.
    for name in spec.names:
        if name in source:
            merged[name] = source[name]

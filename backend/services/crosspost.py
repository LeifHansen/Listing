"""eBay → Etsy: what can be answered for the seller, and what cannot.

A listing imported from an eBay store is written in eBay's vocabulary. Most
of what Etsy wants is already there or can be worked out (mapping_etsy and
field_map say which), and three things cannot be: who made the item, when,
and whether it is a craft supply. Etsy allows only handmade, vintage (20+
years) and supplies, so those are an attestation, not a field — the
crosspost asks once per batch and this module applies the answer, letting
anything already on the listing win over it.

Pure but for one lazy import (the taxonomy lookup, which is HTTP): the
review a seller reads before anything is sent is built here.
"""
from __future__ import annotations

import re

from ..marketplaces import mapping_etsy
from ..models import EtsyFields, Listing

# The item specifics an eBay seller puts an age in. Read in this order, and
# the first that yields a decade wins — "Decade" is unambiguous where "Year"
# on a reprint may be the year of the original.
WHEN_MADE_SPECIFICS = ("Decade", "Era", "Period", "Year Manufactured",
                       "Year of Manufacture", "Vintage", "Year")

_DECADE = re.compile(r"\b(1[7-9]\d0|20[0-2]0)s\b")
_SHORT_DECADE = re.compile(r"\b['’]?([0-9]0)s\b")
_YEAR = re.compile(r"\b(1[7-9]\d\d|20[0-2]\d)\b")


# Etsy's when_made vocabulary as year ranges, most specific first. Derived
# from the vocabulary itself where it can be (the year spans and the
# decades), and stated for the two that cannot: Etsy's "1800s" and "1700s"
# are CENTURIES — its decade buckets stop at 1900s — and the two "before_"
# buckets are catch-alls that must be tried last, or "before_2007" would
# swallow every decade under it and answer "before 2007" for a 1970s bowl.
_CENTURIES = {"1800s": (1800, 1899), "1700s": (1700, 1799)}


def _ranges() -> list[tuple[str, int, int]]:
    spans, catch_alls = [], []
    for bucket in mapping_etsy.WHEN_MADE:
        if bucket == "made_to_order":
            continue
        latest = mapping_etsy.when_made_latest_year(bucket)
        if latest is None:
            continue
        if bucket.startswith("before_"):
            catch_alls.append((bucket, 0, latest))
        elif bucket in _CENTURIES:
            spans.append((bucket, *_CENTURIES[bucket]))
        elif bucket.endswith("s"):
            spans.append((bucket, latest - 9, latest))
        else:
            spans.append((bucket, int(bucket.split("_")[0]), latest))
    # Narrowest first among the spans, so 2000_2006 wins over nothing wider
    # and a decade wins over a century.
    spans.sort(key=lambda r: r[2] - r[1])
    catch_alls.sort(key=lambda r: r[2])
    return spans + catch_alls


def _bucket_for_year(year: int) -> str:
    """Etsy's when_made bucket holding `year`, or "" outside its vocabulary."""
    for bucket, first, last in _ranges():
        if first <= year <= last:
            return bucket
    return ""


def _from_text(text: str) -> str:
    """A decade or a year in free text, as an Etsy when_made bucket."""
    text = text or ""
    decade = _DECADE.search(text)
    if decade:
        return _bucket_for_year(int(decade.group(1)))
    short = _SHORT_DECADE.search(text)
    if short:
        # "90s" on a resale listing means 1990s; "'20s" means 1920s, not the
        # decade that has not finished. Both are the 1900s reading, which is
        # what a vintage seller writing two digits means.
        return _bucket_for_year(1900 + int(short.group(1)))
    year = _YEAR.search(text)
    if year:
        return _bucket_for_year(int(year.group(1)))
    return ""


def when_made_from(listing: Listing) -> str:
    """Etsy's when_made for this listing, read off what the seller already
    wrote — the age item specifics first, then the title. "" when nothing
    in the listing says, which is the crosspost's cue to use the batch
    answer instead of inventing one.
    """
    by_name = {s.name.strip().lower(): s.value for s in (listing.item_specifics or [])}
    for name in WHEN_MADE_SPECIFICS:
        bucket = _from_text(by_name.get(name.lower(), ""))
        if bucket:
            return bucket
    return _from_text(listing.title)


def apply_defaults(listing: Listing, batch: dict) -> EtsyFields:
    """The listing's Etsy fields with the batch's answers filled in.

    Precedence, narrowest first: what the listing already carries, then what
    can be read off the listing (when_made), then the batch default. Never
    the other way round — a seller who set an item's Etsy fields by hand has
    said something more specific than a batch answer.
    """
    fields = listing.etsy.model_copy(deep=True)
    batch = batch or {}
    if fields.who_made not in mapping_etsy.WHO_MADE:
        fields.who_made = str(batch.get("who_made") or "")
    if fields.when_made not in mapping_etsy.WHEN_MADE:
        fields.when_made = when_made_from(listing) or str(batch.get("when_made") or "")
    if not fields.is_supply and batch.get("is_supply"):
        fields.is_supply = True
    for name in ("shipping_profile_id", "return_policy_id", "readiness_state_id"):
        if not getattr(fields, name):
            setattr(fields, name, str(batch.get(name) or ""))
    return fields


def review_row(listing: Listing, settings: dict, batch: dict,
               mode: str = "draft", suggest=None) -> dict:
    """One listing as the seller will review it before anything is sent.

    Everything the crosspost would fill in, shown rather than assumed: the
    tidied title Etsy will get, the category and where it came from, the
    attribution after the batch answers are applied, the tags and materials
    derived from the item's own details — and what Etsy still refuses it
    over. `suggest` is the category lookup (services.etsy.suggest_taxonomy),
    injected so a review can be built without the network.
    """
    fields = apply_defaults(listing, batch)
    proposed = listing.model_copy(deep=True)
    proposed.etsy = fields

    taxonomy = {"id": fields.taxonomy_id, "path": "", "source": "listing"}
    if not fields.taxonomy_id and suggest is not None:
        picked = suggest(proposed) or {}
        taxonomy = {"id": int(picked.get("taxonomy_id") or 0),
                    "path": str(picked.get("path") or ""),
                    "source": str(picked.get("source") or "")}
        proposed.etsy.taxonomy_id = taxonomy["id"]

    issues = mapping_etsy.preflight(proposed, settings or {}, mode)
    blockers = [i for i in issues if i.get("level", "error") == "error"]
    return {
        "id": "",
        "title": listing.title,
        "etsy_title": mapping_etsy.clean_title(listing.title),
        "taxonomy": taxonomy,
        "who_made": proposed.etsy.who_made,
        "when_made": proposed.etsy.when_made,
        "when_made_source": ("listing" if listing.etsy.when_made else
                             "details" if when_made_from(listing) else
                             "batch" if proposed.etsy.when_made else ""),
        "is_supply": proposed.etsy.is_supply,
        "tags": mapping_etsy.build_tags(proposed),
        "materials": mapping_etsy.build_materials(proposed),
        "etsy": proposed.etsy.model_dump(),
        "blockers": blockers,
        "warnings": [i for i in issues if i.get("level") == "warn"],
        "ready": not blockers,
        # Etsy's own rule, worth its own flag because it is the one a
        # reseller meets: something someone else made recently is neither
        # vintage nor handmade, and Etsy does not allow it at all.
        "policy_flag": mapping_etsy.needs_production_partner(proposed),
    }

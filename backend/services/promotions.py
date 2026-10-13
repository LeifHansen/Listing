"""eBay Promoted Listings Standard — promote a just-published live listing.

Promoted Listings Standard uses a COST_PER_SALE funding model: the seller picks
an ad rate (a percentage) and eBay only charges that percentage of the sale
price when the item actually sells through the promotion. We keep ONE
app-managed campaign per marketplace and attach each promoted listing to it as
an ad at the chosen rate (updating the bid if the ad already exists, e.g. on a
re-publish/revise).

Everything here is best-effort and fail-soft: a promotion problem must NEVER
fail a publish — the listing is already live by the time we get here. When the
connected token lacks the sell.marketing scope (a seller who linked eBay before
that scope existed), we surface a clear "reconnect" status instead.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from .. import config
from .ebay import is_scope_error
from ..models import Listing
from . import ebay

log = logging.getLogger("thryft.promotions")

CAMPAIGN_NAME = "Thryft Shop Promoted Listings"
_MARKETING = "/sell/marketing/v1"
# Fallback ad rate when eBay has no recommended rate for a listing (or the
# Recommendation API isn't reachable / the scope isn't granted yet). eBay's
# own per-listing recommendation wins whenever we can get one.
DEFAULT_AD_RATE = 10.0

# Resolve the campaign id once per (marketplace, token) instead of listing
# campaigns on every publish. Keyed by a short, non-sensitive token suffix.
_CAMPAIGN_CACHE: dict[str, str] = {}
# eBay's recommended ad rate per listing, cached briefly.
_RATE_CACHE: dict[str, tuple[float, dict]] = {}
_RATE_TTL = 300


class _ScopeError(Exception):
    """The token can't use the Marketing API — the seller must reconnect."""


class _EbayFailed(RuntimeError):
    """eBay's Marketing API failed on its OWN side creating the campaign (a
    5xx; production has seen errorId 35001, eBay's generic internal error)."""


class _NoCampaign(RuntimeError):
    """The account can hold only one campaign, and none of its campaigns can
    take our ads. The message is what the seller reads."""


class _RuleBasedCampaign(RuntimeError):
    """The campaign our ad was going into is rule-based (eBay's Easy boost or
    a by-rule Seller Hub campaign): it picks its own listings, and eBay
    refused the ad with errorId 35064. Carries the campaign's name."""

    def __init__(self, name: str = ""):
        super().__init__(name)
        self.name = name


# eBay's refusal to create a SECOND campaign: errorId 35304, "Another campaign
# cannot be created at this time, as the user is currently using a feature
# that only supports one campaign." Sellers who run Promoted Listings through
# Seller Hub's simplified, single-campaign set-up get it on every create. Our
# own named campaign can never exist on such an account, so promotion there
# means adding ads to the one campaign the seller already has.
_ONE_CAMPAIGN_ERROR_ID = 35304
_NO_CAMPAIGN_MESSAGE = ("eBay lets this account run only one Promoted "
                        "Listings campaign, and it has no Promoted Listings "
                        "Standard campaign this listing can join. Start one "
                        "in Seller Hub, then turn Promote on again.")

# The one campaign such an account has is usually RULE-BASED: eBay's Easy
# boost (one tap in Seller Hub promotes every listing, current and future, at
# one rate), or a Seller Hub campaign that picks listings by rule. A
# rule-based campaign chooses its own listings; ads cannot be added to it one
# listing at a time, and eBay refuses the attempt with errorId 35064, "This
# operation is only supported for key based campaigns." Production saw it on
# "Easy boost 09/18/2026 12:40:09". getCampaigns marks such a campaign with a
# campaignCriterion container (absent on key-based ones), which is checked
# before any ad is attempted; 35064 at ad time is the backstop.
_RULE_BASED_ERROR_ID = 35064
_RULE_BASED_MESSAGE = ("eBay promotes this account's listings through its own "
                       "campaign, “{name}”, which picks the listings "
                       "and the ad rate itself (Seller Hub's Easy boost does "
                       "this), so the app can't add this listing at its own "
                       "rate. New listings that match it are promoted by it "
                       "automatically. To set a rate per listing, turn that "
                       "campaign off in Seller Hub and start a Promoted "
                       "Listings Standard campaign, then turn Promote on "
                       "again.")
# Accounts whose only campaign is rule-based, by cache key, with when that
# was learned: {key: (time, campaign name)}. Nothing in a publish can change
# it, so promotion on such an account waits _RULE_BASED_TTL between looks
# instead of asking eBay (and being refused) on every promoted publish.
_RULE_BASED: dict[str, tuple[float, str]] = {}
_RULE_BASED_TTL = 30 * 60


# When creating the campaign last failed on eBay's side, per account. Nothing
# about the seller's listing or account can fix a 5xx, and every promoted
# publish after one asked again -- a GET and a POST each, a refusal row in the
# error feed each (x13 in two days), and, in a bulk run, the same failure once
# per listing. For _CREATE_BACKOFF after one, promotion on that account waits.
_CREATE_FAILED: dict[str, float] = {}
_CREATE_BACKOFF = 30 * 60

# What the seller reads instead of eBay's JSON error envelope, which is what
# "Couldn't start the promotion: {exc}" used to put on their screen.
_EBAY_FAILED_MESSAGE = ("eBay couldn't set up the promotion just now — the "
                        "problem is on eBay's side, and the listing itself is "
                        "live. Turn Promote on again in a little while to "
                        "retry.")


# Same JSON+language headers every Sell API call wants; ebay.py owns the one
# definition so a change (an added header, a marketplace id) lands everywhere
# instead of in whichever copy someone happened to edit.
from .ebay import rest_headers as _headers  # noqa: E402


def _cache_key(marketplace: str, token: str) -> str:
    return f"{marketplace}:{token[-12:]}"


def _rule_based_message(name: str) -> str:
    return _RULE_BASED_MESSAGE.format(name=name or "Easy boost")


def _remember_rule_based(cache_key: str, name: str) -> None:
    if len(_RULE_BASED) >= 200:
        _RULE_BASED.pop(min(_RULE_BASED, key=lambda k: _RULE_BASED[k][0]), None)
    _RULE_BASED[cache_key] = (time.time(), name)
    # The id stays usable for nothing; let the next look after the TTL
    # re-read the account's campaigns instead of trusting it.
    _CAMPAIGN_CACHE.pop(cache_key, None)


def _ensure_campaign(client: httpx.Client, base: str, token: str, marketplace: str) -> str:
    cache_key = _cache_key(marketplace, token)
    if cache_key in _CAMPAIGN_CACHE:
        return _CAMPAIGN_CACHE[cache_key]
    known = _RULE_BASED.get(cache_key)
    if known and time.time() - known[0] < _RULE_BASED_TTL:
        raise _NoCampaign(_rule_based_message(known[1]))
    # Reuse our campaign if it already exists on this account.
    r = client.get(f"{base}{_MARKETING}/ad_campaign", headers=_headers(token),
                   params={"campaign_name": CAMPAIGN_NAME})
    if r.status_code == 200:
        for c in (r.json().get("campaigns") or []):
            if c.get("campaignName") == CAMPAIGN_NAME and c.get("campaignId"):
                _CAMPAIGN_CACHE[cache_key] = c["campaignId"]
                return c["campaignId"]
    elif is_scope_error(r):
        raise _ScopeError()
    # Otherwise create it (COST_PER_SALE = pay only when it sells).
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body = {
        "campaignName": CAMPAIGN_NAME,
        "marketplaceId": marketplace,
        "fundingStrategy": {"fundingModel": "COST_PER_SALE"},
        "startDate": now,
    }
    r = client.post(f"{base}{_MARKETING}/ad_campaign", headers=_headers(token), json=body)
    if r.status_code in (200, 201):
        # Create returns the id in the Location header (or body on some paths).
        loc = r.headers.get("Location", "")
        cid = loc.rstrip("/").split("/")[-1] if loc else (r.json() or {}).get("campaignId", "")
        if cid:
            _CAMPAIGN_CACHE[cache_key] = cid
            return cid
    if is_scope_error(r):
        raise _ScopeError()
    if r.status_code >= 500:
        raise _EbayFailed(f"campaign create failed ({r.status_code}): {r.text[:200]}")
    if _is_one_campaign_refusal(r):
        cid, rule_based_name = _sellers_own_campaign(client, base, token, marketplace)
        if not cid:
            if rule_based_name is not None:
                _remember_rule_based(cache_key, rule_based_name)
                raise _NoCampaign(_rule_based_message(rule_based_name))
            raise _NoCampaign(_NO_CAMPAIGN_MESSAGE)
        log.info("promoting into the account's single campaign %s", cid)
        _CAMPAIGN_CACHE[cache_key] = cid
        return cid
    raise RuntimeError(f"campaign create failed ({r.status_code}): {r.text[:200]}")


def _is_one_campaign_refusal(resp: httpx.Response) -> bool:
    if resp.status_code != 409:
        return False
    try:
        errors = (resp.json() or {}).get("errors") or []
    except ValueError:
        errors = []
    if any(e.get("errorId") == _ONE_CAMPAIGN_ERROR_ID for e in errors
           if isinstance(e, dict)):
        return True
    return "only supports one c" in resp.text.lower()


def _sellers_own_campaign(client: httpx.Client, base: str, token: str,
                          marketplace: str) -> tuple[str, str | None]:
    """The campaign a one-campaign account already has, if our ads can go in
    it: COST_PER_SALE (our ads are bid percentages, which a cost-per-click
    campaign cannot take), key-based (a rule-based one picks its own
    listings and takes no ads by listing), and not ended. A running one wins
    over a paused or scheduled one. Returns (campaign id, None); when there
    is none, ("", name of the rule-based campaign that was the only live
    candidate) or ("", None) when there was not even that."""
    r = client.get(f"{base}{_MARKETING}/ad_campaign", headers=_headers(token),
                   params={"limit": 100})
    if is_scope_error(r):
        raise _ScopeError()
    if r.status_code != 200:
        return "", None
    usable = []
    rule_based: list[tuple[int, str]] = []
    for c in (r.json().get("campaigns") or []):
        if not isinstance(c, dict) or not c.get("campaignId"):
            continue
        model = str((c.get("fundingStrategy") or {}).get("fundingModel") or "")
        if model and model.upper() != "COST_PER_SALE":
            continue
        if c.get("marketplaceId") and c["marketplaceId"] != marketplace:
            continue
        status = str(c.get("campaignStatus") or "").upper()
        if status in ("ENDED", "DELETED"):
            continue
        rank = 0 if status in ("RUNNING", "ENDING_SOON") else 1
        if c.get("campaignCriterion"):
            rule_based.append((rank, str(c.get("campaignName") or "")))
            continue
        usable.append((rank, c["campaignId"]))
    usable.sort()
    if usable:
        return usable[0][1], None
    rule_based.sort()
    return "", (rule_based[0][1] if rule_based else None)


def _rule_based_refusal(resp: httpx.Response) -> str | None:
    """The campaign name eBay put in its errorId 35064 refusal, or "" when
    the refusal names none; None when the response is not that refusal."""
    try:
        errors = (resp.json() or {}).get("errors") or []
    except ValueError:
        errors = []
    for e in errors:
        if not isinstance(e, dict) or e.get("errorId") != _RULE_BASED_ERROR_ID:
            continue
        for prm in e.get("parameters") or []:
            if isinstance(prm, dict) and prm.get("name") == "campaignName":
                return str(prm.get("value") or "")
        return ""
    return None


def _create_or_update_ad_by_listing(client: httpx.Client, base: str, token: str,
                                    campaign_id: str, listing_id: str,
                                    rate: float) -> dict:
    """Ad for a listing eBay knows by ITEM ID — anything published through the
    Trading API. The inventory-reference route below only works for listings
    the Sell Inventory API created; using it for a Trading listing fails with
    "inventory reference not found", which is how promotions silently stopped
    working for every listing after the publish path moved to Trading."""
    req = {"listingId": str(listing_id), "bidPercentage": f"{rate:.1f}"}
    r = client.post(
        f"{base}{_MARKETING}/ad_campaign/{campaign_id}/bulk_create_ads_by_listing_id",
        headers=_headers(token), json={"requests": [req]})
    if r.status_code in (200, 201, 207) and "error" not in r.text.lower():
        return {"ok": True}
    if is_scope_error(r):
        raise _ScopeError()
    refused = _rule_based_refusal(r)
    if refused is not None:
        raise _RuleBasedCampaign(refused)
    # Already advertised (a re-publish or a second promote) → move its bid.
    u = client.post(
        f"{base}{_MARKETING}/ad_campaign/{campaign_id}/bulk_update_ads_bid_by_listing_id",
        headers=_headers(token), json={"requests": [req]})
    if u.status_code in (200, 207) and "error" not in u.text.lower():
        return {"ok": True, "updated": True}
    return {"ok": False, "detail": (r.text or u.text)[:300]}


def _create_or_update_ad(client: httpx.Client, base: str, token: str,
                         campaign_id: str, sku: str, rate: float) -> dict:
    payload = {
        "bidPercentage": f"{rate:.1f}",
        "inventoryReferenceId": sku,
        "inventoryReferenceType": "INVENTORY_ITEM",
    }
    r = client.post(
        f"{base}{_MARKETING}/ad_campaign/{campaign_id}/create_ads_by_inventory_reference",
        headers=_headers(token), json=payload)
    if r.status_code in (200, 201, 207):
        return {"ok": True}
    if is_scope_error(r):
        raise _ScopeError()
    refused = _rule_based_refusal(r)
    if refused is not None:
        raise _RuleBasedCampaign(refused)
    # Ad already exists (re-publish) → update its bid to the new rate instead.
    if r.status_code == 409 or "already" in r.text.lower():
        u = client.post(
            f"{base}{_MARKETING}/ad_campaign/{campaign_id}"
            "/bulk_update_ads_bid_by_inventory_reference",
            headers=_headers(token), json={"requests": [payload]})
        if u.status_code in (200, 207):
            return {"ok": True, "updated": True}
        return {"ok": False, "detail": u.text[:300]}
    return {"ok": False, "detail": r.text[:300]}


def promote_listing(session_id: str, listing: Listing, creds: dict | None) -> dict:
    """Best-effort promote a just-published live listing. Returns a status dict:
    {promoted, ad_rate, needs_reconnect, message}. Never raises."""
    rate = round(float(listing.ad_rate_percent or 0), 1)
    if not listing.promote or rate <= 0:
        return {"promoted": False}
    token = (creds or {}).get("access_token")
    if not token:
        return {"promoted": False, "needs_reconnect": True,
                "message": "Connect your eBay account to run promotions."}
    base = config.EBAY_API_BASE
    marketplace = config.EBAY_MARKETPLACE_ID
    account = f"{marketplace}:{(creds or {}).get('_uid') or token[-12:]}"
    failed_at = _CREATE_FAILED.get(account)
    if failed_at and time.time() - failed_at < _CREATE_BACKOFF:
        log.info("promote skipped (session=%s): eBay failed to create the "
                 "campaign %.0fs ago", session_id, time.time() - failed_at)
        return {"promoted": False, "message": _EBAY_FAILED_MESSAGE}
    # Which handle eBay knows this listing by: a Trading-published (or
    # imported) listing is addressed by item id; an Inventory-API listing by
    # the SKU we gave its inventory item.
    by_listing_id = bool(listing.ebay_listing_id) and (listing.source or "") == "ebay"
    try:
        with httpx.Client(timeout=30) as client:
            campaign_id = _ensure_campaign(client, base, token, marketplace)
            if by_listing_id:
                res = _create_or_update_ad_by_listing(
                    client, base, token, campaign_id, listing.ebay_listing_id, rate)
            else:
                res = _create_or_update_ad(client, base, token, campaign_id,
                                           ebay.sku_for(session_id), rate)
    except _ScopeError:
        return {"promoted": False, "needs_reconnect": True,
                "message": "Reconnect your eBay account to grant ad permissions, "
                           "then republish to start the promotion."}
    except _NoCampaign as exc:
        log.warning("promote failed (session=%s): %s", session_id, exc)
        return {"promoted": False, "message": str(exc)}
    except _RuleBasedCampaign as exc:
        _remember_rule_based(_cache_key(marketplace, token), exc.name)
        log.warning("promote failed (session=%s): campaign %r is rule-based",
                    session_id, exc.name)
        return {"promoted": False, "message": _rule_based_message(exc.name)}
    except _EbayFailed as exc:
        if len(_CREATE_FAILED) >= 200:
            _CREATE_FAILED.pop(min(_CREATE_FAILED, key=_CREATE_FAILED.get), None)
        _CREATE_FAILED[account] = time.time()
        log.warning("promote failed (session=%s): %s", session_id, exc)
        return {"promoted": False, "message": _EBAY_FAILED_MESSAGE}
    except Exception as exc:  # noqa: BLE001 - promotion must never break publish
        log.warning("promote failed (session=%s): %s", session_id, exc)
        return {"promoted": False, "message": f"Couldn't start the promotion: {exc}"}
    if res.get("ok"):
        log.info("promoted session=%s at %.1f%%", session_id, rate)
        return {"promoted": True, "ad_rate": rate,
                "message": f"Promoted at {rate:.1f}% — you're only charged that if it "
                           "sells through the promotion."}
    return {"promoted": False,
            "message": "eBay couldn't start the promotion: "
                       + (res.get("detail") or "unknown error")}


def _num(val):
    try:
        r = round(float(val), 1)
        return r if r > 0 else None
    except (TypeError, ValueError):
        return None


def _extract_bid(rec: dict):
    """Pull eBay's suggested ad-rate % out of one listing recommendation.

    eBay's shape (Recommendation API, findListingRecommendations):
        {"listingId": "...",
         "marketing": {"ad": {"bidPercentages": [{"basis": "TRENDING",
                                                  "value": "10.5"}], ...}}}
    Two bases can come back:
      ITEM     - tailored to this item (attributes, seasonality, competition)
      TRENDING - the category-level average: what recently-sold listings in
                 this item's category were paying
    Prefer the tailored one, fall back to the category average.

    (The old code read `marketingRecommendations.ad.bidPercentages` as a dict
    of basic/suggested — a shape eBay never returns — so every lookup came
    back empty and every listing silently got the flat default rate.)
    """
    ad = ((rec.get("marketing") or rec.get("marketingRecommendations") or {})
          .get("ad") or {})
    bids = ad.get("bidPercentages")
    if isinstance(bids, list):
        by_basis = {str(b.get("basis", "")).upper(): b.get("value")
                    for b in bids if isinstance(b, dict)}
        for key in ("ITEM", "TRENDING"):
            got = _num(by_basis.get(key))
            if got:
                return got
        # An unexpected basis label still carries a usable number.
        return next((v for v in (_num(b.get("value")) for b in bids
                                 if isinstance(b, dict)) if v), None)
    if isinstance(bids, dict):  # defensive: older/other shapes
        return next((v for v in (_num(bids.get(k)) for k in
                                 ("item", "trending", "basic", "suggested")) if v), None)
    return None


def suggested_ad_rates(creds: dict | None, listing_ids: list[str]) -> dict[str, float]:
    """eBay's recommended Promoted Listings ad rate (%) per eBay listing id, via
    the Sell Recommendation API. Best-effort and cached; returns {} when the
    scope isn't granted or nothing is recommended. {listing_id: rate}."""
    token = (creds or {}).get("access_token")
    ids = sorted({str(i) for i in listing_ids if i})
    if not token or not ids:
        return {}
    cache_key = f"{token[-12:]}:{','.join(ids)}"
    hit = _RATE_CACHE.get(cache_key)
    if hit and time.time() - hit[0] < _RATE_TTL:
        return hit[1]
    out: dict[str, float] = {}
    try:
        r = httpx.post(
            f"{config.EBAY_API_BASE}/sell/recommendation/v1/find",
            headers={**_headers(token),
                     "X-EBAY-C-MARKETPLACE-ID": config.EBAY_MARKETPLACE_ID},
            params={"filter": "recommendationTypes:{AD}"},
            json={"listingIds": ids[:500]},
            timeout=30,
        )
        if r.status_code == 200:
            for rec in r.json().get("listingRecommendations", []) or []:
                lid = str(rec.get("listingId") or "")
                bid = _extract_bid(rec)
                if lid and bid:
                    out[lid] = bid
        else:
            log.info("ad-rate recommendations %s: %s", r.status_code, r.text[:160])
    except Exception as exc:  # noqa: BLE001 - recommendations are optional
        log.info("ad-rate recommendations unavailable: %s", exc)
    if len(_RATE_CACHE) > 200:
        _RATE_CACHE.clear()
    _RATE_CACHE[cache_key] = (time.time(), out)
    return out

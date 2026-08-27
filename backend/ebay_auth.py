"""'Sign in with eBay' OAuth (Authorization Code grant) + auto-discovery of
business policies and inventory location via the Account/Inventory APIs.

This is what removes the manual publish secrets: the seller clicks "Connect
eBay", grants access, and we store a refresh token + their policy IDs.
"""
from __future__ import annotations

import time
from typing import Optional
from urllib.parse import urlencode

import httpx

from . import config
from .config import log


def authorize_url(state: str) -> str:
    params = {
        "client_id": config.EBAY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.EBAY_RUNAME,
        "scope": " ".join(config.EBAY_OAUTH_SCOPES),
        "state": state,
    }
    return f"{config.EBAY_AUTH_BASE}/oauth2/authorize?{urlencode(params)}"


class AccountApiError(RuntimeError):
    """A Sell Account API write eBay refused, carrying eBay's own words.

    Separate from OAuthError because these fail for entirely different reasons
    — not eligible for a program, a policy name already taken, a category type
    the account can't use — and the caller's job is to relay eBay's sentence
    rather than map it to an auth bucket.
    """

    def __init__(self, message: str, *, description: str = "", status: int = 0):
        super().__init__(message)
        self.description = description
        self.status = status


class OAuthError(RuntimeError):
    """A token request eBay refused, carrying the reason eBay gave.

    `raise_for_status()` alone reduces every refusal to a status code, and the
    body it discards is the only thing that says WHICH of the half-dozen
    causes it was. Connecting is the one flow a seller cannot debug from the
    UI — a bare "connection failed" leaves nobody, seller or operator, with
    anywhere to start.
    """

    def __init__(self, message: str, *, code: str = "", description: str = "",
                 status: int = 0):
        super().__init__(message)
        self.code = code
        self.description = description
        self.status = status

    @property
    def reason(self) -> str:
        """A short bucket for the UI: what the seller can actually do.

        `invalid_client` is the app's own credentials, and `invalid_grant` on
        this flow is nearly always a redirect_uri (RuName) that doesn't match
        the keyset — neither is the seller's to fix, and both look identical
        when the app is pointed at sandbox while they signed in to production
        eBay, so they share one bucket.
        """
        if self.code in ("invalid_client", "unauthorized_client"):
            return "config"
        if self.code == "invalid_grant":
            return "expired"
        return "unknown"


def _oauth_error(resp: httpx.Response) -> OAuthError:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    code = str(body.get("error") or "")
    description = str(body.get("error_description") or "")[:300]
    return OAuthError(
        f"eBay refused the token request ({resp.status_code}"
        + (f", {code}" if code else "") + ")",
        code=code, description=description, status=resp.status_code)


def _token_request(data: dict) -> dict:
    resp = httpx.post(
        f"{config.EBAY_API_BASE}/identity/v1/oauth2/token",
        data=data,
        auth=(config.EBAY_CLIENT_ID, config.EBAY_CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if not resp.is_success:
        err = _oauth_error(resp)
        # eBay's own words, at WARNING, against the environment in use: the
        # single most common cause of a connect that will not stick is an app
        # pointed at sandbox while the seller signs in to production (or the
        # reverse), and that is invisible from the status code alone.
        log.warning("ebay oauth: %s [env=%s] %s", err, config.EBAY_ENV,
                    err.description)
        raise err
    return resp.json()


def exchange_code(code: str) -> dict:
    """Trade an authorization code for access + refresh tokens."""
    body = _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.EBAY_RUNAME,
        }
    )
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": time.time() + float(body.get("expires_in", 7200)),
    }


def refresh_access_token(refresh_token: str) -> dict:
    # Deliberately omit `scope`: a refresh grant may only request scopes that
    # were in the original consent, so sending the full (possibly newly-widened)
    # list would break connections made before a scope was added. Omitting it
    # returns a token with exactly the scopes the user originally granted.
    body = _token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    )
    return {
        "access_token": body["access_token"],
        "expires_at": time.time() + float(body.get("expires_in", 7200)),
    }


def fetch_user_identity(access_token: str) -> dict:
    """The connected seller's identity (username, email). Requires the
    commerce.identity.readonly scope — connections made before that scope was
    added won't have it and this will 403 until the user reconnects."""
    # The Identity API lives on the apiz.* host, not api.*.
    base = config.EBAY_API_BASE.replace("://api.", "://apiz.")
    resp = httpx.get(
        f"{base}/commerce/identity/v1/user/",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def identity_display(identity: dict) -> dict:
    """Flatten the Identity API response to {username, email} for the UI."""
    acct = identity.get("individualAccount") or identity.get("businessAccount") or {}
    return {
        "username": identity.get("username") or "",
        "email": acct.get("email") or "",
    }


def _account_get(path: str, access_token: str) -> dict:
    resp = httpx.get(
        f"{config.EBAY_API_BASE}{path}",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        params={"marketplace_id": config.EBAY_MARKETPLACE_ID},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_payments_program(access_token: str) -> dict:
    """Payments-program opt-in status for the connected seller.

    Bank accounts are linked on eBay's side (Seller Hub -> Payments); the
    closest signal the API exposes is the payments-program status, which is
    OPTED_IN once payout setup (including a bank account) is complete.
    """
    resp = httpx.get(
        f"{config.EBAY_API_BASE}/sell/account/v1/payments_program/"
        f"{config.EBAY_MARKETPLACE_ID}/EBAY_PAYMENTS",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# Business policies are a seller PROGRAM on eBay, not a default. Until an
# account is opted in, every policy list comes back empty and every policy id
# is rejected — which is exactly the "the dropdowns are empty and I can't
# publish" state, with nothing on screen naming the cause.
SELLING_POLICY_MANAGEMENT = "SELLING_POLICY_MANAGEMENT"


def opted_in_programs(access_token: str) -> Optional[set[str]]:
    """The programs this account is opted into, or None when eBay didn't say.

    None is deliberately not an empty set. "We couldn't ask" and "opted into
    nothing" lead to opposite advice — the second tells a seller to opt in, the
    first tells them nothing is known — and collapsing them is the mistake this
    integration has made in four other places.
    """
    try:
        data = _account_get("/sell/account/v1/program/get_opted_in_programs",
                            access_token)
    except Exception as exc:  # noqa: BLE001 - unknown, not "none"
        log.info("ebay: couldn't read opted-in programs: %s", exc)
        return None
    return {p.get("programType") for p in (data.get("programs") or [])
            if p.get("programType")}


def opt_in_to_program(access_token: str,
                      program: str = SELLING_POLICY_MANAGEMENT) -> None:
    """Opt the connected account into an eBay seller program.

    eBay documents this as taking up to 24 hours to take effect, and it
    returns no payload, so a 2xx means "accepted", never "in force". Callers
    must not tell the seller their policies are ready — only that eBay has the
    request. Raises on refusal so the caller can say what eBay said.
    """
    resp = httpx.post(
        f"{config.EBAY_API_BASE}/sell/account/v1/program/opt_in",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={"programType": program},
        timeout=30,
    )
    if not resp.is_success:
        raise AccountApiError(
            f"eBay refused the opt-in ({resp.status_code})",
            description=resp.text[:300], status=resp.status_code)
    log.info("ebay: opted in to %s", program)


def fetch_privileges(access_token: str) -> Optional[dict]:
    """{registration_complete, selling_limit} for the connected seller, or
    None when eBay didn't answer.

    Both halves are things that stop a publish for a reason no listing field
    explains: an unfinished registration, and the monthly selling limit —
    error 21919188, which this app spent a release reporting as a duplicate
    submission. Knowing them BEFORE a publish is the difference between a
    checklist item and a rejection.

    `sellingLimit` is absent for accounts eBay does not cap, which is why it
    is None rather than zero: a missing cap is not a cap of nothing.
    """
    try:
        data = _account_get("/sell/account/v1/privilege", access_token)
    except Exception as exc:  # noqa: BLE001 - best effort
        log.info("ebay: couldn't read selling privileges: %s", exc)
        return None
    limit = data.get("sellingLimit") or {}
    amount = limit.get("amount") or {}
    return {
        "registration_complete": bool(data.get("sellerRegistrationCompleted")),
        "selling_limit": {
            "amount": amount.get("value"),
            "currency": amount.get("currency"),
            "quantity": limit.get("quantity"),
        } if limit else None,
    }


def ensure_inventory_location(access_token: str, postal_code: str,
                              country: str = "US") -> str:
    """Ensure our own ship-from location holds exactly the seller's ZIP+country
    and return its key. publishOffer needs the location's address to carry a
    country ('Item.Country empty' otherwise). We always use our own key rather
    than reusing an arbitrary existing location, so the ZIP the seller entered
    is the one eBay actually ships from (and can't silently diverge)."""
    base = config.EBAY_API_BASE
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Content-Language": "en-US",
    }
    key = "thryft-loc-1"
    addr = {"country": country, "postalCode": postal_code}
    body = {
        "location": {"address": addr},
        "locationTypes": ["WAREHOUSE"],
        "merchantLocationStatus": "ENABLED",
        # Display name only — the location is found by its key ("thryft-loc-1"
        # above), never by name, so this is safe to keep in step with the brand.
        "name": "Thryft Shop ship-from location",
    }
    r = httpx.post(f"{base}/sell/inventory/v1/location/{key}",
                   headers=headers, json=body, timeout=30)
    # "Already exists" arrives as 409 per the docs but as 400 (error 25801) in
    # practice — treating only 409 as exists made every re-save of Settings
    # fail on an account that already had the location.
    already_exists = r.status_code == 409 or (
        r.status_code == 400 and ("already exists" in r.text.lower()
                                  or "25801" in r.text))
    if already_exists:
        # Force it to the current ZIP+country (it may hold a stale/missing
        # address), and surface it if the repair itself fails.
        u = httpx.post(
            f"{base}/sell/inventory/v1/location/{key}/update_location_details",
            headers=headers, json={"location": {"address": addr}}, timeout=30)
        if u.status_code not in (200, 204):
            log.warning("update_location_details(%s) -> %s %s",
                        key, u.status_code, u.text[:200])
    elif r.status_code not in (200, 204):
        r.raise_for_status()
    return key


_POLICY_SPECS = {
    "fulfillment": ("/sell/account/v1/fulfillment_policy", "fulfillmentPolicies", "fulfillmentPolicyId"),
    "payment": ("/sell/account/v1/payment_policy", "paymentPolicies", "paymentPolicyId"),
    "return": ("/sell/account/v1/return_policy", "returnPolicies", "returnPolicyId"),
}


def list_business_policies(access_token: str) -> dict:
    """All of the seller's eBay business policies, grouped by type, as
    [{id, name, summary}] so the user can pick a default for each."""
    out = {"fulfillment": [], "payment": [], "return": []}
    for key, (path, list_field, id_field) in _POLICY_SPECS.items():
        try:
            data = _account_get(path, access_token)
            for p in data.get(list_field, []):
                entry = {
                    "id": p.get(id_field, ""),
                    "name": p.get("name", "") or p.get(id_field, ""),
                    "summary": _policy_summary(key, p),
                }
                # The shipping-service picker needs to know what each
                # fulfillment policy actually ships with.
                if key == "fulfillment":
                    entry["services"] = [s["code"] for s in _policy_services(p)]
                out[key].append(entry)
        except Exception:  # noqa: BLE001 - best effort per type
            pass
    return out


def policy_ids_on_account(access_token: str) -> dict[str, set[str]]:
    """{kind: {policy ids that actually exist on the connected account}}.

    A saved policy id is only usable on the account that owns it: eBay rejects
    another seller's profile id outright. This is what lets a reconnect keep
    the seller's choices when they still exist and quietly re-pick a default
    when they don't, instead of guessing from the account name — which is
    unreadable on connections made before the identity scope was granted.
    A kind that couldn't be fetched is absent (not empty), so a transient API
    failure never reads as "none of your policies exist".
    """
    out: dict[str, set[str]] = {}
    for kind, (path, list_field, id_field) in _POLICY_SPECS.items():
        try:
            data = _account_get(path, access_token)
        except Exception:  # noqa: BLE001 - unknown, not empty
            continue
        out[kind] = {p.get(id_field, "") for p in data.get(list_field, [])
                     if p.get(id_field)}
    return out


def location_keys_on_account(access_token: str) -> Optional[set[str]]:
    """Every merchantLocationKey on the connected account, or None if the
    lookup failed (unknown — callers must not treat that as "none")."""
    try:
        resp = httpx.get(
            f"{config.EBAY_API_BASE}/sell/inventory/v1/location",
            headers={"Authorization": f"Bearer {access_token}",
                     "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        return {loc.get("merchantLocationKey", "")
                for loc in (resp.json().get("locations") or [])
                if loc.get("merchantLocationKey")}
    except Exception:  # noqa: BLE001
        return None


def _policy_summary(kind: str, p: dict) -> str:
    """A short human hint about what a policy does, for the picker."""
    try:
        if kind == "return":
            if not p.get("returnsAccepted", False):
                return "No returns"
            days = (p.get("returnPeriod") or {}).get("value")
            return f"{days}-day returns" if days else "Returns accepted"
        if kind == "fulfillment":
            codes = [s_["code"] for s_ in _policy_services(p)]
            if codes:
                pretty = ", ".join(_friendly_service(c) for c in codes[:3])
                return pretty + ("…" if len(codes) > 3 else "")
            return "Shipping configured"
        if kind == "payment":
            return "Managed payments"
    except Exception:  # noqa: BLE001
        pass
    return ""


def fetch_policies_and_location(access_token: str) -> dict:
    """Best-effort auto-discovery of the seller's default policies + location.

    Returns whatever it can find; missing pieces stay empty (the seller may
    need to create business policies in Seller Hub first).
    """
    out = {
        "fulfillment_policy_id": "",
        "payment_policy_id": "",
        "return_policy_id": "",
        "merchant_location_key": "",
    }
    for kind, (path, list_field, id_field) in _POLICY_SPECS.items():
        try:
            data = _account_get(path, access_token)
            items = data.get(list_field, [])
            if items:
                # Fulfillment: prefer a USPS Ground Advantage policy (cheapest
                # broadly-applicable service) over whatever happens to be first.
                pick = items[0]
                if kind == "fulfillment":
                    pick = next((p for p in items if _is_ground_policy(p)), pick)
                out[f"{kind}_policy_id"] = pick.get(id_field, "")
        except Exception:  # noqa: BLE001 - best effort
            pass
    try:
        resp = httpx.get(
            f"{config.EBAY_API_BASE}/sell/inventory/v1/location",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        locs = resp.json().get("locations", [])
        if locs:
            out["merchant_location_key"] = locs[0].get("merchantLocationKey", "")
    except Exception:  # noqa: BLE001
        pass
    return out


def ship_from_postal(access_token: str, merchant_location_key: str = "") -> str:
    """The postal code of the seller's ship-from location, read off eBay.

    Publishing through the Trading API has to state where the item ships from
    — eBay rejects the listing outright with "Your item's location was not
    filled in" otherwise. The app usually has the ZIP saved, but a seller who
    connected before we started storing it (or who set their location up on
    eBay directly) has only a merchantLocationKey, so fetch the address behind
    it. Returns "" when it can't be determined; never raises."""
    try:
        resp = httpx.get(
            f"{config.EBAY_API_BASE}/sell/inventory/v1/location",
            headers={"Authorization": f"Bearer {access_token}",
                     "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            return ""
        locs = resp.json().get("locations", []) or []
    except Exception:  # noqa: BLE001 - best-effort lookup
        return ""
    if not locs:
        return ""
    match = next((loc for loc in locs
                  if loc.get("merchantLocationKey") == merchant_location_key),
                 locs[0])
    address = (match.get("location") or {}).get("address") or {}
    return str(address.get("postalCode") or "").strip()


def account_overview(access_token: str) -> dict:
    """Mirror the seller's most-updated eBay account settings, best-effort.
    Every section is fetched independently, so one failing leaves the rest
    intact (e.g. a seller with no business policies still gets locations)."""
    out: dict = {"policies": {"fulfillment": [], "payment": [], "return": []},
                 "locations": [], "programs": [], "payments": {},
                 "programs_known": False, "privileges": None}
    try:
        out["policies"] = list_business_policies(access_token)
    except Exception:  # noqa: BLE001
        pass
    try:
        resp = httpx.get(
            f"{config.EBAY_API_BASE}/sell/inventory/v1/location",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code == 200:
            out["locations"] = resp.json().get("locations", []) or []
    except Exception:  # noqa: BLE001
        pass
    programs = opted_in_programs(access_token)
    # `programs_known` is the whole point of the tri-state: an empty list here
    # used to mean both "opted into nothing" and "eBay didn't answer", and the
    # UI can only offer "turn on business policies" honestly for the first.
    out["programs_known"] = programs is not None
    out["programs"] = sorted(programs or ())
    out["privileges"] = fetch_privileges(access_token)
    try:
        out["payments"] = fetch_payments_program(access_token)
    except Exception:  # noqa: BLE001
        pass
    return out


# ---------------------------------------------------------------------------
# Shipping services (per-listing selection + USPS Ground Advantage default)
# ---------------------------------------------------------------------------

# These policy names show up in the seller's own eBay account, so they carry
# the app's real brand. They are display-only: find_policy_for_service matches
# on the shipping SERVICE CODE, never the name, so renaming them cannot orphan
# a policy an existing seller already has.
GROUND_POLICY_NAME = "USPS Ground Advantage (Thryft Shop)"

# The eBay (EBAY_US) shipping services a seller can one-tap into a fulfillment
# policy. Codes are eBay ShippingServiceCodeType values; keep to well-known,
# calculated-cost-friendly services so policy creation can't send an invalid
# combo. The picker shows label + note; `cap_oz` mirrors preflight's weight
# caps where a service silently kills a publish.
SHIPPING_SERVICES = [
    {"code": "USPSGroundAdvantage", "carrier": "USPS",
     "label": "USPS Ground Advantage", "note": "Cheapest for most packages — up to 70 lb"},
    {"code": "USPSPriority", "carrier": "USPS",
     "label": "USPS Priority Mail", "note": "1–3 business days"},
    {"code": "USPSPriorityExpress", "carrier": "USPS",
     "label": "USPS Priority Mail Express", "note": "Overnight to 2-day"},
    {"code": "USPSMedia", "carrier": "USPS",
     "label": "USPS Media Mail", "note": "Books & media only — slow but cheap"},
    {"code": "US_eBayStandardEnvelope", "carrier": "USPS",
     "label": "eBay Standard Envelope", "note": "Cards & flat items, 3 oz max", "cap_oz": 3},
    {"code": "UPSGround", "carrier": "UPS",
     "label": "UPS Ground", "note": "1–5 business days, good for heavy items"},
    {"code": "UPS3DaySelect", "carrier": "UPS",
     "label": "UPS 3 Day Select", "note": "3 business days"},
    {"code": "UPS2ndDay", "carrier": "UPS",
     "label": "UPS 2nd Day Air", "note": "2 business days"},
    {"code": "FedExHomeDelivery", "carrier": "FEDEX",
     "label": "FedEx Home Delivery", "note": "1–5 business days, residential"},
]


def service_by_code(code: str) -> Optional[dict]:
    norm = (code or "").lower().replace("_", "")
    for svc in SHIPPING_SERVICES:
        if svc["code"].lower().replace("_", "") == norm:
            return svc
    return None


def _policy_services(p: dict) -> list[dict]:
    """[{code, name}] for every shipping service in a fulfillment policy."""
    services = []
    for opt in p.get("shippingOptions") or []:
        for svc in opt.get("shippingServices") or []:
            code = svc.get("shippingServiceCode", "") or ""
            if code:
                services.append({
                    "code": code,
                    "name": svc.get("shippingCarrierCode", "") or "",
                })
    return services


def _is_ground_policy(p: dict) -> bool:
    return any("groundadvantage" in s["code"].lower().replace("_", "")
               for s in _policy_services(p))


def find_policy_for_service(access_token: str, service_code: str) -> Optional[dict]:
    """The seller's first fulfillment policy already shipping `service_code`."""
    norm = (service_code or "").lower().replace("_", "")
    path, list_field, id_field = _POLICY_SPECS["fulfillment"]
    try:
        data = _account_get(path, access_token)
    except Exception:  # noqa: BLE001
        return None
    for p in data.get(list_field, []):
        if any(s["code"].lower().replace("_", "") == norm
               for s in _policy_services(p)):
            return {"id": p.get(id_field, ""), "name": p.get("name", "")}
    return None


def ensure_service_policy(access_token: str, svc: dict) -> dict:
    """Find — or create — a fulfillment policy that ships `svc` (an entry from
    SHIPPING_SERVICES): calculated cost, domestic, 2-day handling. Returns
    {id, name, created}."""
    existing = find_policy_for_service(access_token, svc["code"])
    if existing and existing["id"]:
        return {**existing, "created": False}
    name = (f"{svc['label']} (Thryft Shop)"
            if svc["code"] != "USPSGroundAdvantage" else GROUND_POLICY_NAME)
    body = {
        "name": name,
        "marketplaceId": config.EBAY_MARKETPLACE_ID,
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
        "handlingTime": {"value": 2, "unit": "DAY"},
        "shippingOptions": [{
            "costType": "CALCULATED",
            "optionType": "DOMESTIC",
            "shippingServices": [{
                "sortOrder": 1,
                "shippingCarrierCode": svc["carrier"],
                "shippingServiceCode": svc["code"],
            }],
        }],
    }
    resp = httpx.post(
        f"{config.EBAY_API_BASE}/sell/account/v1/fulfillment_policy",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    created = resp.json()
    log.info("ebay: created %s fulfillment policy %s", svc["code"],
             created.get("fulfillmentPolicyId", ""))
    return {"id": created.get("fulfillmentPolicyId", ""),
            "name": created.get("name", name), "created": True}


# Defaults for the policies the app creates. They are arguments rather than
# constants so the Settings screen can own them next; these are the values a
# small seller wants on day one, not opinions worth hard-coding forever.
DEFAULT_RETURN_DAYS = 30
DEFAULT_RETURN_PAYER = "BUYER"
PAYMENT_POLICY_NAME = "Immediate payment (Thryft Shop)"
RETURN_POLICY_NAME = "30-day returns (Thryft Shop)"


def _create_policy(kind: str, access_token: str, body: dict) -> dict:
    """POST one business policy, returning {id, name}. Raises AccountApiError
    with eBay's own words, which for policy writes is the whole story — "name
    already used", "not opted in", "category type not allowed" all arrive as
    the same 400 otherwise."""
    path, _list_field, id_field = _POLICY_SPECS[kind]
    resp = httpx.post(
        f"{config.EBAY_API_BASE}{path}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    if not resp.is_success:
        raise AccountApiError(
            f"eBay refused to create the {kind} policy ({resp.status_code})",
            description=resp.text[:300], status=resp.status_code)
    created = resp.json()
    log.info("ebay: created %s policy %s", kind, created.get(id_field, ""))
    return {"id": created.get(id_field, ""),
            "name": created.get("name", body.get("name", ""))}


def _first_existing_policy(kind: str, access_token: str) -> Optional[dict]:
    """The account's first policy of this kind, or None.

    None covers both "none exist" and "couldn't ask" on purpose here: the only
    caller creates one either way, and creating a second policy is recoverable
    while publishing with none is not.
    """
    path, list_field, id_field = _POLICY_SPECS[kind]
    try:
        items = _account_get(path, access_token).get(list_field) or []
    except Exception as exc:  # noqa: BLE001
        log.info("ebay: couldn't list %s policies: %s", kind, exc)
        return None
    if not items:
        return None
    return {"id": items[0].get(id_field, ""), "name": items[0].get("name", "")}


def ensure_payment_policy(access_token: str,
                          immediate_pay: bool = True) -> dict:
    """Find — or create — a payment policy. Returns {id, name, created}.

    On a managed-payments account eBay handles the money, so the policy is
    little more than a name and the immediate-pay setting; sending
    paymentMethods here is what gets these rejected. Immediate pay is on by
    default because an unpaid fixed-price sale is the small seller's most
    common headache.
    """
    existing = _first_existing_policy("payment", access_token)
    if existing and existing["id"]:
        return {**existing, "created": False}
    body = {
        "name": PAYMENT_POLICY_NAME,
        "marketplaceId": config.EBAY_MARKETPLACE_ID,
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
        "immediatePay": immediate_pay,
    }
    return {**_create_policy("payment", access_token, body), "created": True}


def ensure_return_policy(access_token: str,
                         days: int = DEFAULT_RETURN_DAYS,
                         payer: str = DEFAULT_RETURN_PAYER) -> dict:
    """Find — or create — a return policy. Returns {id, name, created}.

    `returnPeriod` is required whenever returns are accepted, and refundMethod
    is deprecated to MONEY_BACK (any other value is rejected), so it is sent
    as the only legal value rather than left out and defaulted server-side.
    """
    existing = _first_existing_policy("return", access_token)
    if existing and existing["id"]:
        return {**existing, "created": False}
    body = {
        "name": RETURN_POLICY_NAME,
        "marketplaceId": config.EBAY_MARKETPLACE_ID,
        "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
        "returnsAccepted": True,
        "returnPeriod": {"value": int(days), "unit": "DAY"},
        "returnShippingCostPayer": payer,
        "refundMethod": "MONEY_BACK",
    }
    return {**_create_policy("return", access_token, body), "created": True}


def fulfillment_policy_services(access_token: str, policy_id: str) -> list[dict]:
    """[{code, name}] for one fulfillment policy (empty on any failure —
    preflight treats unknown services as unconstrained)."""
    services, _ = fulfillment_policy_lookup(access_token, policy_id)
    return services


def fulfillment_policy_lookup(access_token: str,
                              policy_id: str) -> tuple[list[dict], Optional[bool]]:
    """([{code, name}], exists) for one fulfillment policy.

    `exists` is False only when eBay positively says this account has no such
    policy (404), True when it returned one, and None when the answer is
    unknown (network trouble, an unexpected status). The three are kept apart
    because "this policy isn't on your account" is a real, fixable publish
    blocker — it's what a policy id left over from a different eBay account
    looks like — while "we couldn't ask" must never be reported as one.
    """
    if not policy_id:
        return [], None
    try:
        p = _account_get(f"/sell/account/v1/fulfillment_policy/{policy_id}",
                         access_token)
        return _policy_services(p), True
    except httpx.HTTPStatusError as exc:
        # 404 is eBay saying the policy is not on this account. 400 is eBay
        # rejecting the REQUEST — a malformed id, a marketplace it won't answer
        # for — which says nothing about what the account has, and reading it
        # as absence blocks every live publish behind a shipping-policy error
        # the seller cannot act on.
        if exc.response.status_code == 404:
            return [], False
        return [], None
    except Exception:  # noqa: BLE001
        return [], None


_SERVICE_FRIENDLY = [
    ("standardenvelope", "eBay Standard Envelope (3 oz max)"),
    ("groundadvantage", "USPS Ground Advantage"),
    ("firstclass", "USPS First Class (<1 lb)"),
    ("uspspriorityflatrate", "USPS Priority Flat Rate"),
    ("uspspriorityexpress", "USPS Priority Express"),
    ("uspspriority", "USPS Priority"),
    ("mediamail", "USPS Media Mail"),
    ("upsground", "UPS Ground"),
    ("fedex", "FedEx"),
]


def _friendly_service(code: str) -> str:
    c = (code or "").lower().replace("_", "")
    for frag, name in _SERVICE_FRIENDLY:
        if frag in c:
            return name
    return code

"""Marketplace provider registry.

Providers register themselves on import; loading is lazy (the same
credential-gated-provider idea as config.bg_engine_chain, but for whole
marketplaces). The registry module itself stays import-light so tests can
import it under CI's minimal install — the heavy provider modules (httpx,
sqlalchemy via db) are only pulled in by _ensure_loaded(), which runs at
request time from main.py, never from tests. (config is the one import here
that does anything at import time, and every test already has it: conftest
imports it first, under a scratch DATA_DIR, for exactly that reason.)

Adding marketplace N+1 = one provider module, one import line in
_ensure_loaded(), and its key in config.MARKETPLACE_KEYS — the roster the
launch gate is drawn from. A provider missing from that tuple registers fine
and is then invisible, which is a silent way to ship nothing; the registry
test asserts the two agree so the omission fails CI instead.
"""
from __future__ import annotations

from typing import Optional

from .. import config
from .base import MarketplaceProvider

_REGISTRY: dict[str, MarketplaceProvider] = {}
_ORDER: list[str] = []
_LOADED = False


def register(provider: MarketplaceProvider) -> None:
    """Register a provider (idempotent; last registration wins)."""
    if provider.key not in _REGISTRY:
        _ORDER.append(provider.key)
    _REGISTRY[provider.key] = provider


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    # Each provider module self-registers at import. The ROSTER's order is
    # settled by _ordered_keys rather than by which of these lands first, so
    # a provider module imported from somewhere else cannot reorder it.
    from . import ebay_provider  # noqa: F401
    from . import etsy_provider  # noqa: F401
    from . import depop_provider  # noqa: F401


def _ordered_keys() -> list[str]:
    """Registered keys in the order the APP declares (config.MARKETPLACE_KEYS),
    with anything registered that the config does not name after them, in the
    order it registered.

    Not registration order, which is what this used to be: providers
    self-register at module import, so the order was whichever module python
    happened to import first. `_ensure_loaded` imports eBay first to make
    that come out right — and any other import of a provider module,
    anywhere, beat it to the registry and silently reordered the roster.
    A test file that imported etsy_provider to instantiate the class was
    enough. The roster is the first thing a seller reads and eBay leads it,
    so the order is stated rather than inherited.
    """
    ranked = {key: i for i, key in enumerate(config.MARKETPLACE_KEYS)}
    return sorted(_ORDER, key=lambda k: (ranked.get(k, len(ranked)), _ORDER.index(k)))


def get(key: str) -> Optional[MarketplaceProvider]:
    """The provider for `key`, or None — including None for a marketplace this
    deployment doesn't offer (config.marketplace_enabled).

    Withheld and unknown deliberately answer the same way. Every caller
    already handles None as "no such marketplace": the {marketplace} routes
    404, the publish fan-out returns "Unknown marketplace 'etsy'", and the
    inbox drops the source. Giving a switched-off marketplace its own answer
    would mean teaching each of those a second failure mode, for a state no
    seller can reach through a UI built from the roster below.
    """
    _ensure_loaded()
    if not config.marketplace_enabled(key):
        return None
    return _REGISTRY.get(key)


def all_providers() -> list[MarketplaceProvider]:
    """Every provider this deployment offers, in MARKETPLACE_KEYS order.

    The gate lives here rather than in _ensure_loaded() so a withheld
    marketplace stays imported and constructible: its module still
    self-registers, every_provider() can still see it, and its tests still
    run against the real class. Switching it on is an env var, not a deploy
    of un-commented code.
    """
    _ensure_loaded()
    return [_REGISTRY[k] for k in _ordered_keys() if config.marketplace_enabled(k)]


def every_provider() -> list[MarketplaceProvider]:
    """Every REGISTERED provider, launch gate ignored — for diagnostics and
    for the test that keeps config.MARKETPLACE_KEYS honest. Not for anything
    seller-facing: that is all_providers()."""
    _ensure_loaded()
    return [_REGISTRY[k] for k in _ordered_keys()]


def available() -> list[MarketplaceProvider]:
    """Providers whose operator-side OAuth credentials are configured — the
    only ones the UI offers to connect or publish to."""
    return [p for p in all_providers() if p.oauth_ready()]


def coming_soon(provider: MarketplaceProvider) -> tuple[bool, str]:
    """(should the UI say "coming soon", seller-facing note).

    True while a provider declares `coming_soon` and its credentials aren't
    in place: access is pending on the marketplace's side (a partner-API
    application under review), which is a different story from a deployment
    that simply hasn't set its env vars — and a very different thing to show
    a seller. Self-clears the moment oauth_ready() flips.
    """
    if not getattr(provider, "coming_soon", False) or provider.oauth_ready():
        return False, ""
    return True, getattr(provider, "coming_soon_note", "")


def access_pending(provider: MarketplaceProvider,
                   uid: Optional[str]) -> tuple[bool, str]:
    """(is this seller blocked by the marketplace itself, seller-facing note).

    The sibling of coming_soon(), for the opposite situation: the credentials
    are configured and the integration works, but the marketplace only lets
    certain accounts authorize it. Etsy is the case — its app tiers seat a
    fixed number of shops (one, on the seller app Etsy registers by default)
    and Etsy enforces that on its own consent page, after the seller has left
    this site. There is no callback to turn into an error, so the check has to
    happen before the redirect.

    Per-user, unlike coming_soon(): the accounts holding those seats are
    precisely the ones that CAN connect, so this must be able to say yes to
    one seller and no to the next. Providers opt in with an access_pending(uid)
    method; everyone else is unaffected.
    """
    if not provider.oauth_ready():
        return False, ""     # "not set up" is a different story, told first
    check = getattr(provider, "access_pending", None)
    if not callable(check) or not check(uid):
        return False, ""
    return True, getattr(provider, "access_pending_note", "")


def access_unverified(provider: MarketplaceProvider) -> tuple[bool, str]:
    """(is the marketplace's wall still up with nobody here to vouch, note).

    The third answer between access_pending()'s two. Pending means "we know
    this seller is not allowed through"; not pending normally means "we know
    they are". This is the case where the deployment knows neither: the
    marketplace still restricts who may authorize, and nothing here records
    who — so the seller is sent out to find out from the marketplace, on a
    page that never redirects back.

    Not a refusal, unlike access_pending(). The seller may well be the one
    account that works (on a deploy with no roster they usually are), and
    turning them away would be the worse guess. It drives a caution shown
    before the redirect, so the marketplace's refusal arrives as a named
    outcome with a next step instead of a dead end.

    Not per-user either, for the same reason it exists: if the deployment
    could tell these sellers apart, access_pending() would already be
    answering. Providers opt in with an access_unverified() method.
    """
    if not provider.oauth_ready():
        return False, ""     # "not set up" is a different story, told first
    check = getattr(provider, "access_unverified", None)
    if not callable(check) or not check():
        return False, ""
    return True, getattr(provider, "access_unverified_note", "")

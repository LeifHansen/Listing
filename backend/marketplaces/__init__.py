"""Marketplace provider registry.

Providers register themselves on import; loading is lazy (the same
credential-gated-provider idea as config.bg_engine_chain, but for whole
marketplaces). The registry module itself stays import-light so tests can
import it under CI's minimal install — the heavy provider modules (httpx,
sqlalchemy via db) are only pulled in by _ensure_loaded(), which runs at
request time from main.py, never from tests.

Adding marketplace N+1 = one provider module + one import line in
_ensure_loaded().
"""
from __future__ import annotations

from typing import Optional

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
    # Each provider module self-registers at import. eBay first: it's the
    # flagship and the default target for legacy single-marketplace publishes.
    from . import ebay_provider  # noqa: F401
    from . import etsy_provider  # noqa: F401
    from . import depop_provider  # noqa: F401


def get(key: str) -> Optional[MarketplaceProvider]:
    _ensure_loaded()
    return _REGISTRY.get(key)


def all_providers() -> list[MarketplaceProvider]:
    _ensure_loaded()
    return [_REGISTRY[k] for k in _ORDER]


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

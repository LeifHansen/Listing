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

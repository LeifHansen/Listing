"""Marketplace provider contract.

Import-light on purpose: stdlib + pydantic models only, no httpx and no
sqlalchemy, so the registry and the pure per-marketplace mapping modules can
be imported by tests under CI's minimal install (which has neither).

A provider is one marketplace's complete integration behind a uniform
surface: OAuth configuration/connection, a per-user credentials bundle, and
the listing lifecycle (preflight/publish/end). The publish orchestrator in
main.py only ever talks to this surface — everything marketplace-specific
(eBay's Trading-vs-Inventory split, Etsy's PKCE + image uploads, Depop's
partner API) stays inside the provider module that owns it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from ..models import Listing


@dataclass
class PublishContext:
    """Everything a provider needs to publish one listing for one user."""
    session_id: str
    listing: Listing
    mode: str                    # "draft" | "live"
    base_url: str                # public origin, for building /media image URLs
    uid: Optional[str]           # logged-in user id, None for anonymous flows
    prev_record: dict            # db.get_listing() snapshot at request time ({} if new)


@dataclass
class PublishOutcome:
    """One marketplace's result for one publish attempt.

    `status` is the marketplace-state this attempt establishes ("published",
    "draft", "ended") — empty when the attempt should not change recorded
    state (errors, imported-draft saves). A failed attempt reports through
    ok=False + message/issues and never rewrites lifecycle state.

    `raw` carries the exact legacy JSON body for single-eBay publishes so the
    orchestrator can return it verbatim — old clients (BulkMode, the iOS
    wrapper) keep seeing byte-identical responses.
    """
    ok: bool
    listing_id: str = ""
    url: str = ""                # public view URL on the marketplace
    message: str = ""
    dry_run: bool = False
    status: str = ""             # "" | "draft" | "published" | "ended"
    issues: list = field(default_factory=list)   # {target, level, title, fix} dicts
    raw: dict = field(default_factory=dict)


@runtime_checkable
class MarketplaceProvider(Protocol):
    key: str      # short id used in routes, DB rows, JSON and the UI ("ebay")
    label: str    # display name ("eBay")

    # --- configuration / connection ---
    def oauth_ready(self) -> bool:
        """Operator-side credentials present (the config.py predicate)."""
        ...

    def oauth_missing(self) -> list[str]:
        """Names (never values) of the absent env vars behind oauth_ready."""
        ...

    def authorize_url(self, state: str) -> tuple[str, dict]:
        """(redirect URL, flow secrets to stash in the flow cookie — e.g.
        Etsy's PKCE code_verifier; {} when the flow needs nothing extra)."""
        ...

    def exchange_code(self, code: str, flow: dict) -> dict:
        """Trade the callback code for tokens; returns the account fields to
        persist (refresh_token, external_username, external_id, settings)."""
        ...

    def account_status(self, uid: Optional[str]) -> dict:
        """Connection status for Settings (the /api/{key}/status body)."""
        ...

    def creds_for(self, uid: Optional[str]) -> Optional[dict]:
        """Live per-user credentials bundle, or None when not connected /
        token refresh failed (publishing then falls back to dry-run)."""
        ...

    def disconnect(self, uid: str) -> None: ...

    # --- listing lifecycle ---
    def supports(self) -> dict:
        """Capabilities: {draft, edit, end, auction: bool, max_photos: int}."""
        ...

    def preflight(self, uid: Optional[str], listing: Listing,
                  mode: str) -> list[dict]:
        """Pre-publish checklist; issue dicts in the ebay_errors shape
        {target, level, title, fix} so the editor can jump to fixes."""
        ...

    def publish(self, ctx: PublishContext,
                creds: Optional[dict]) -> PublishOutcome: ...

    def end(self, ctx: PublishContext, creds: dict) -> dict: ...

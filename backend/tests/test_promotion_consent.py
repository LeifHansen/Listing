"""Enrolling a seller in a fee-bearing ad programme needs them to say yes.

Promoted Listings Standard is COST_PER_SALE: eBay takes a percentage of the
sale price when an item sells through the promotion, at a default ad rate of
10%. `auto_promote_enabled` returned True in two situations that are not
consent:

  1. The preference is ABSENT — i.e. every seller who has never opened
     Settings and saved that field. Silence is not agreement to a fee.
  2. The preference could not be READ. db.get_prefs returns {} on a database
     failure, which is indistinguishable from "never set", so an outage
     enrolled sellers too.

The second is indefensible under any reading of the product: whatever the
right default is, "we could not find out" is never a yes. The first is a
deliberate reversal of a product choice — the old docstring says sellers
complained about publishes landing unpromoted — so it is made explicitly and
consent is recorded, rather than inferred from silence in the other direction.
"""
from __future__ import annotations

from backend.marketplaces import ebay_provider


def test_a_seller_who_never_chose_is_not_enrolled(monkeypatch):
    """Silence is not consent to a percentage of every sale."""
    monkeypatch.setattr(ebay_provider.db, "get_prefs", lambda _uid: {})
    assert ebay_provider.auto_promote_enabled("u1") is False


def test_an_unreadable_preference_never_enrolls(monkeypatch):
    """The sharp one. A database blip used to turn into an advertising
    commitment, and nothing anywhere recorded that it had."""
    def _boom(_uid):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(ebay_provider.db, "get_prefs", _boom)
    assert ebay_provider.auto_promote_enabled("u1") is False


def test_an_explicit_yes_is_honoured(monkeypatch):
    """Opting in still works — this is about how consent is established, not
    about removing the feature."""
    monkeypatch.setattr(ebay_provider.db, "get_prefs",
                        lambda _uid: {"auto_promote": True})
    assert ebay_provider.auto_promote_enabled("u1") is True


def test_an_explicit_no_is_honoured(monkeypatch):
    monkeypatch.setattr(ebay_provider.db, "get_prefs",
                        lambda _uid: {"auto_promote": False})
    assert ebay_provider.auto_promote_enabled("u1") is False


def test_an_anonymous_publish_is_never_promoted(monkeypatch):
    """Unchanged, and for the same reason: there is nobody to have agreed."""
    assert ebay_provider.auto_promote_enabled(None) is False
    assert ebay_provider.auto_promote_enabled("") is False


def test_a_per_listing_opt_in_still_promotes(monkeypatch):
    """The seller ticking Promote on THIS listing is explicit consent for
    this listing, and does not depend on the account default."""
    from backend.models import Listing

    monkeypatch.setattr(ebay_provider.db, "get_prefs", lambda _uid: {})
    listing = Listing(title="A lamp", promote=True)

    assert listing.promote is True
    assert ebay_provider.auto_promote_enabled("u1") is False

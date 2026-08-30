"""The per-seller access gate in the marketplace registry.

Pure-module tests: import only marketplaces (which imports marketplaces.base,
stdlib + pydantic), so they run under CI's minimal install. The providers here
are fakes on purpose — the rule being tested belongs to the registry, not to
Etsy, and a real provider would drag httpx and sqlalchemy in with it.
"""
from backend import marketplaces


class _Provider:
    """The smallest thing the registry helpers actually touch."""
    key = "fake"
    label = "Fake"

    def __init__(self, ready=True, pending=None, note=None):
        self._ready = ready
        if pending is not None:
            self.access_pending = pending
        if note is not None:
            self.access_pending_note = note

    def oauth_ready(self):
        return self._ready


def test_a_provider_that_does_not_opt_in_is_never_pending():
    """eBay and Depop declare nothing, so the gate must not touch them."""
    assert marketplaces.access_pending(_Provider(), "u1") == (False, "")


def test_pending_for_this_seller_returns_the_note():
    note = "They're reviewing us."
    p = _Provider(pending=lambda uid: True, note=note)
    assert marketplaces.access_pending(p, "u1") == (True, note)


def test_not_pending_for_the_owner():
    """The whole point of making this per-user: one account can connect."""
    p = _Provider(pending=lambda uid: uid == "stranger", note="wait")
    assert marketplaces.access_pending(p, "owner") == (False, "")
    assert marketplaces.access_pending(p, "stranger") == (True, "wait")


def test_unconfigured_beats_pending():
    """With no credentials there is nothing to be approved FOR, and the
    operator's missing-env explainer is the more useful thing to show."""
    p = _Provider(ready=False, pending=lambda uid: True, note="wait")
    assert marketplaces.access_pending(p, "u1") == (False, "")


def test_an_opted_in_provider_without_a_note_still_gates():
    """The gate is the safety property; the copy is a nicety."""
    p = _Provider(pending=lambda uid: True)
    assert marketplaces.access_pending(p, "u1") == (True, "")


def test_the_anonymous_caller_is_passed_through_to_the_provider():
    """The roster is built for logged-out visitors too, so uid can be None and
    the provider — not the registry — decides what that means."""
    seen = []
    p = _Provider(pending=lambda uid: (seen.append(uid), True)[1], note="wait")
    assert marketplaces.access_pending(p, None) == (True, "wait")
    assert seen == [None]

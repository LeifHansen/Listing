"""Marketplace registry: ordering, credential gating, unknown keys.

CI can't import the real provider modules (httpx isn't installed there), so
these tests mark the registry as loaded and register lightweight fakes —
exactly what the lazy-loading split exists to allow.
"""
import backend.marketplaces as mp
import pytest


class _Fake:
    def __init__(self, key, label, ready):
        self.key, self.label, self._ready = key, label, ready

    def oauth_ready(self):
        return self._ready


@pytest.fixture()
def clean_registry():
    saved = (dict(mp._REGISTRY), list(mp._ORDER), mp._LOADED)
    mp._REGISTRY.clear()
    mp._ORDER.clear()
    mp._LOADED = True  # skip real provider imports (they need httpx)
    yield mp
    mp._REGISTRY.clear()
    mp._REGISTRY.update(saved[0])
    mp._ORDER[:] = saved[1]
    mp._LOADED = saved[2]


def test_registration_order_preserved(clean_registry):
    a = _Fake("alpha", "Alpha", True)
    b = _Fake("beta", "Beta", True)
    mp.register(a)
    mp.register(b)
    assert [p.key for p in mp.all_providers()] == ["alpha", "beta"]
    assert mp.get("alpha") is a
    assert mp.get("beta") is b


def test_reregistration_keeps_position_and_replaces(clean_registry):
    mp.register(_Fake("alpha", "Alpha", True))
    mp.register(_Fake("beta", "Beta", True))
    replacement = _Fake("alpha", "Alpha2", True)
    mp.register(replacement)
    assert [p.key for p in mp.all_providers()] == ["alpha", "beta"]
    assert mp.get("alpha") is replacement


def test_available_filters_on_oauth_ready(clean_registry):
    mp.register(_Fake("alpha", "Alpha", True))
    mp.register(_Fake("beta", "Beta", False))
    assert [p.key for p in mp.available()] == ["alpha"]


def test_unknown_key_returns_none(clean_registry):
    assert mp.get("nope") is None

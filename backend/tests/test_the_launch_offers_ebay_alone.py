"""The launch gate: eBay ships to the mobile app on its own.

Etsy and Depop are built, mapped and tested, and withheld from every
seller-facing surface until we come back to them. The point of doing that
with a variable instead of commenting the providers out is everything these
tests assert: the code stays in the tree and reachable, the roster stops
offering it, and turning one back on is one env var rather than a deploy of
un-commented code.

The gate is read through config, so these tests patch config.marketplaces_
enabled() the way the every_marketplace fixture does — the variable itself is
read once at import, and reloading config here would take the app's module
graph with it.
"""
from __future__ import annotations

import pytest

from backend import config
import backend.marketplaces as mp


@pytest.fixture()
def enabled(monkeypatch):
    """Set the gate to an explicit list of keys, as the operator would."""
    def _set(*keys: str):
        monkeypatch.setattr(config, "marketplaces_enabled", lambda: tuple(keys))
        return mp
    return _set


# --- what the seller is offered --------------------------------------------

def test_the_default_offers_ebay_alone(enabled):
    """No variable set anywhere — a laptop, a CI run, the Fly app, the mobile
    build. Withheld has to be the DEFAULT, or "for the time being" lasts
    exactly as long as somebody remembers to set something."""
    assert config._MARKETPLACES_ENABLED == "", (
        "the suite is running with MARKETPLACES_ENABLED set; conftest scrubs "
        "it so this file measures the code and not a developer's shell")
    assert config.marketplaces_enabled() == ("ebay",)


def test_the_roster_the_ui_is_built_from_hides_them(enabled):
    """all_providers() is what /api/marketplaces answers with, and the whole
    frontend is drawn from that answer: the Settings connection cards, the
    publish-target chips, the Etsy and Depop panels in the editor."""
    enabled("ebay")
    assert [p.key for p in mp.all_providers()] == ["ebay"]


def test_the_withheld_provider_is_still_registered(enabled):
    """The half that a commented-out import would have cost us. The module
    still imports, still self-registers, and its class is still there to be
    tested — the gate hides it from sellers, it does not delete it."""
    enabled("ebay")
    assert [p.key for p in mp.every_provider()] == ["ebay", "etsy", "depop"]


def test_a_withheld_marketplace_is_not_handed_out(enabled):
    """get() answers None, which every caller already treats as "no such
    marketplace": the {marketplace} routes 404, the publish fan-out refuses
    the target, the inbox drops the source."""
    enabled("ebay")
    assert mp.get("etsy") is None
    assert mp.get("depop") is None
    assert mp.get("ebay") is not None


def test_available_cannot_reintroduce_a_withheld_marketplace(enabled):
    """available() filters all_providers() on credentials. Etsy's ARE
    configured in production (Etsy approved the app), so this is the one
    place a working integration could have leaked back into the UI."""
    enabled("ebay")
    assert "etsy" not in [p.key for p in mp.available()]


# --- turning one back on ---------------------------------------------------

def test_naming_a_marketplace_switches_it_back_on(enabled):
    """The revisit path, and the reason this is a variable: no code changes."""
    enabled("ebay", "etsy")
    assert [p.key for p in mp.all_providers()] == ["ebay", "etsy"]
    assert mp.get("etsy") is not None
    assert mp.get("depop") is None


def test_the_order_is_the_registrys_not_the_variables(enabled):
    """eBay stays first however the list is typed — it is the flagship and
    the default target for legacy single-marketplace publishes, and the
    chips are drawn in this order."""
    enabled("depop", "etsy", "ebay")
    assert [p.key for p in mp.all_providers()] == ["ebay", "etsy", "depop"]


# --- parsing the variable --------------------------------------------------
# These go through the real parser rather than the fixture, so they measure
# what an operator's typing actually does.

def _reloaded(monkeypatch, value):
    monkeypatch.setattr(config, "_MARKETPLACES_ENABLED", value)
    return config


def test_all_means_everything_registered(monkeypatch):
    cfg = _reloaded(monkeypatch, "all")
    assert cfg.marketplaces_enabled() == cfg.MARKETPLACE_KEYS


def test_a_list_is_read_through_padding_and_case(monkeypatch):
    """Typed by hand into a secrets dashboard, once."""
    cfg = _reloaded(monkeypatch, " eBay , Etsy ")
    assert cfg.marketplaces_enabled() == ("ebay", "etsy")


def test_a_blank_value_is_the_launch_default_not_an_empty_roster(monkeypatch):
    """A secret cleared to "" is what every other variable in config reads as
    unset, and the alternative here is an app with no marketplace at all."""
    for value in ("", "   ", ",", " , "):
        cfg = _reloaded(monkeypatch, value.strip())
        assert cfg.marketplaces_enabled() == ("ebay",), value


def test_ebay_cannot_be_switched_off_by_this_variable(monkeypatch):
    """The publish and revise paths reach for eBay by name, so a list that
    omitted it would not launch a leaner app — it would 500 the publish
    button. A typo in one env var must not be able to take the product down.
    """
    cfg = _reloaded(monkeypatch, "etsy")
    assert "ebay" in cfg.marketplaces_enabled()
    assert cfg.marketplace_enabled("ebay") is True


def test_a_misspelled_key_is_ignored_and_reported(monkeypatch):
    """Fails closed and looks identical to never having touched the variable:
    the marketplace the operator meant to switch back on stays hidden."""
    cfg = _reloaded(monkeypatch, "ebay,etsi")
    assert cfg.marketplaces_enabled() == ("ebay",)
    warning = [w for w in cfg.config_warnings() if "MARKETPLACES_ENABLED" in w]
    assert warning and "etsi" in warning[0]


def test_omitting_ebay_is_reported_rather_than_obeyed(monkeypatch):
    """The quieter half of the same idea: forcing eBay on is right, and
    letting the operator believe their list turned it off is not."""
    cfg = _reloaded(monkeypatch, "etsy")
    warning = [w for w in cfg.config_warnings() if "MARKETPLACES_ENABLED" in w]
    assert warning and "ebay" in warning[0]


def test_the_launch_default_and_all_are_both_silent(monkeypatch):
    """Nothing to report in either of the two states an operator arrives at
    deliberately — warnings that fire on correct configuration train people
    to ignore the line that means something."""
    assert _reloaded(monkeypatch, "").config_warnings() == []
    assert _reloaded(monkeypatch, "all").config_warnings() == []
    assert _reloaded(monkeypatch, "ebay").config_warnings() == []
    assert _reloaded(monkeypatch, "ebay,etsy,depop").config_warnings() == []


# --- the gate and the registry cannot drift --------------------------------

def test_every_registered_provider_is_named_in_the_roster():
    """The silent failure this gate could ship: marketplace N+1 registers a
    provider, nobody adds its key to config.MARKETPLACE_KEYS, and it is
    either invisible forever or ungateable forever depending on which way the
    default falls. Neither is discoverable from the roster. So the two lists
    have to agree, and CI is where that gets found out — not a seller
    wondering where the marketplace went."""
    assert ([p.key for p in mp.every_provider()]
            == list(config.MARKETPLACE_KEYS))

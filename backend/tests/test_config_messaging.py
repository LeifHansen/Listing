"""The commerce.message scope gate.

These are the regression guard on an outage, not a feature test. Requesting an
OAuth scope eBay hasn't approved for the keyset fails the ENTIRE consent
screen, so a stray append here would stop every seller from connecting eBay —
and publishing dies with the connection. The scope must therefore stay absent
until an operator opts in.
"""

_MESSAGE = "https://api.ebay.com/oauth/api_scope/commerce.message"
_LOGISTICS = "https://api.ebay.com/oauth/api_scope/sell.logistics"


def test_messaging_off_by_default(fresh_config):
    cfg = fresh_config()
    assert cfg.EBAY_MESSAGING_ENABLED is False
    assert _MESSAGE not in cfg.EBAY_OAUTH_SCOPES


def test_messaging_scope_appended_once_when_enabled(fresh_config):
    cfg = fresh_config(EBAY_MESSAGING_ENABLED="1")
    assert cfg.EBAY_MESSAGING_ENABLED is True
    assert cfg.EBAY_OAUTH_SCOPES.count(_MESSAGE) == 1


def test_messaging_flag_accepts_the_usual_spellings(fresh_config):
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        assert fresh_config(EBAY_MESSAGING_ENABLED=truthy).EBAY_MESSAGING_ENABLED
    for falsy in ("", "0", "false", "no", "off"):
        assert not fresh_config(EBAY_MESSAGING_ENABLED=falsy).EBAY_MESSAGING_ENABLED


def test_messaging_leaves_the_other_scopes_alone(fresh_config):
    base = fresh_config().EBAY_OAUTH_SCOPES
    on = fresh_config(EBAY_MESSAGING_ENABLED="1").EBAY_OAUTH_SCOPES
    assert [s for s in on if s != _MESSAGE] == base


def test_messaging_and_logistics_are_independent(fresh_config):
    """Two limited-release flags, separately approved by eBay — enabling one
    must never smuggle in the other's scope."""
    cfg = fresh_config(EBAY_MESSAGING_ENABLED="1")
    assert _LOGISTICS not in cfg.EBAY_OAUTH_SCOPES
    cfg = fresh_config(EBAY_LOGISTICS_ENABLED="1")
    assert _MESSAGE not in cfg.EBAY_OAUTH_SCOPES
    cfg = fresh_config(EBAY_MESSAGING_ENABLED="1", EBAY_LOGISTICS_ENABLED="1")
    assert _MESSAGE in cfg.EBAY_OAUTH_SCOPES and _LOGISTICS in cfg.EBAY_OAUTH_SCOPES

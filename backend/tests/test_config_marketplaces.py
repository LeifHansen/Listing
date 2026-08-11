"""Etsy/Depop credential gating in config (via the fresh_config fixture)."""


def test_etsy_not_ready_by_default(fresh_config):
    cfg = fresh_config()
    assert not cfg.etsy_oauth_ready()
    assert cfg.ETSY_CLIENT_ID == ""


def test_etsy_ready_with_keystring_and_redirect(fresh_config):
    cfg = fresh_config(ETSY_CLIENT_ID="key123",
                       ETSY_REDIRECT_URI="https://app.example/api/etsy/callback")
    assert cfg.etsy_oauth_ready()


def test_etsy_keystring_fallback_env_name(fresh_config):
    cfg = fresh_config(ETSY_KEYSTRING="key123",
                       ETSY_REDIRECT_URI="https://app.example/api/etsy/callback")
    assert cfg.ETSY_CLIENT_ID == "key123"
    assert cfg.etsy_oauth_ready()


def test_etsy_placeholder_value_treated_unset(fresh_config):
    cfg = fresh_config(ETSY_CLIENT_ID="<paste your keystring>",
                       ETSY_REDIRECT_URI="https://app.example/api/etsy/callback")
    assert cfg.ETSY_CLIENT_ID == ""
    assert not cfg.etsy_oauth_ready()


def test_depop_requires_every_partner_var(fresh_config):
    partial = fresh_config(DEPOP_CLIENT_ID="id", DEPOP_CLIENT_SECRET="secret")
    assert not partial.depop_oauth_ready()
    almost = fresh_config(
        DEPOP_CLIENT_ID="id", DEPOP_CLIENT_SECRET="secret",
        DEPOP_AUTH_URL="https://partnerapi.depop.com/oauth/authorize",
        DEPOP_TOKEN_URL="https://partnerapi.depop.com/oauth/token")
    assert not almost.depop_oauth_ready()   # redirect URI still missing
    full = fresh_config(
        DEPOP_CLIENT_ID="id", DEPOP_CLIENT_SECRET="secret",
        DEPOP_AUTH_URL="https://partnerapi.depop.com/oauth/authorize",
        DEPOP_TOKEN_URL="https://partnerapi.depop.com/oauth/token",
        DEPOP_REDIRECT_URI="https://app.example/api/depop/callback")
    assert full.depop_oauth_ready()


def test_depop_api_base_default_and_override(fresh_config):
    assert fresh_config().DEPOP_API_BASE == "https://partnerapi.depop.com"
    cfg = fresh_config(DEPOP_API_BASE="https://sandbox.partnerapi.depop.com")
    assert cfg.DEPOP_API_BASE == "https://sandbox.partnerapi.depop.com"

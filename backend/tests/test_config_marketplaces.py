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


# --- monetization readiness -------------------------------------------------

def test_tokens_missing_names_every_gap(fresh_config):
    """A half-configured paid tier must be visible, not silent."""
    cfg = fresh_config()
    assert set(cfg.tokens_missing()) == {
        "TOKENS_ENABLED", "DATABASE_URL", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"}
    assert cfg.tokens_enabled() is False


def test_tokens_missing_is_empty_when_fully_configured(fresh_config):
    cfg = fresh_config(TOKENS_ENABLED="true",
                       DATABASE_URL="postgresql://u:p@h/db",
                       STRIPE_SECRET_KEY="sk_live_abc",
                       STRIPE_WEBHOOK_SECRET="whsec_abc")
    assert cfg.tokens_missing() == []
    assert cfg.tokens_enabled() is True


def test_metering_on_without_stripe_is_reported(fresh_config):
    """The worst configuration: users are metered but cannot buy more, so
    they hit a wall with no way through. It must not look healthy."""
    cfg = fresh_config(TOKENS_ENABLED="true", DATABASE_URL="postgresql://u:p@h/db")
    assert cfg.tokens_enabled() is True          # metering IS live
    assert "STRIPE_SECRET_KEY" in cfg.tokens_missing()


def test_stripe_live_mode_distinguishes_key_types(fresh_config):
    assert fresh_config(STRIPE_SECRET_KEY="sk_live_abc").stripe_live_mode() is True
    assert fresh_config(STRIPE_SECRET_KEY="sk_test_abc").stripe_live_mode() is False
    assert fresh_config().stripe_live_mode() is None


# --- "you set something, just not this" -------------------------------------
# Production shipped with STRIPE_API_SECRET_KEY deployed while the code reads
# STRIPE_SECRET_KEY, so /api/health reported the key as missing while the Fly
# dashboard showed one plainly set. tokens_missing() was right and unhelpful;
# these are the cases that make it say which mistake was made.

def test_stripe_secret_reads_the_api_prefixed_name_too(fresh_config):
    """STRIPE_API_SECRET_KEY is the name the production keyset is deployed
    under. Accepted as a second name for the same setting, like DATABASE_URL
    takes NEON_PRODUCTION_DATABASE_URL — renaming a live secret restarts the
    machine, and with min_machines_running = 1 that means restarting it under
    whatever batch is in flight."""
    cfg = fresh_config(STRIPE_API_SECRET_KEY="sk_live_abc")
    assert cfg.STRIPE_SECRET_KEY == "sk_live_abc"
    assert cfg.stripe_ready() is True
    assert "STRIPE_SECRET_KEY" not in cfg.tokens_missing()


def test_the_canonical_name_still_wins(fresh_config):
    cfg = fresh_config(STRIPE_SECRET_KEY="sk_live_canonical",
                       STRIPE_API_SECRET_KEY="sk_live_other")
    assert cfg.STRIPE_SECRET_KEY == "sk_live_canonical"


def test_a_resolved_alias_is_not_reported_as_a_near_miss(fresh_config):
    """Nothing to warn about once the setting works under either name."""
    cfg = fresh_config(STRIPE_API_SECRET_KEY="sk_live_abc")
    assert cfg.config_warnings() == []


def test_a_key_that_is_not_a_secret_key_is_called_out(fresh_config):
    """Reading the secret from either name makes this worth its own check: a
    publishable pk_ in the slot satisfies every readiness check in the app and
    then fails at the one moment that matters."""
    cfg = fresh_config(STRIPE_API_SECRET_KEY="pk_live_wrong")
    assert cfg.stripe_ready() is True          # present...
    assert cfg.stripe_live_mode() is None      # ...but not a secret key
    assert any("isn't a secret key" in w for w in cfg.config_warnings())


def test_near_miss_is_still_reported_for_a_name_with_no_alias(fresh_config):
    """The detector still earns its keep on the settings that have only one
    accepted spelling."""
    cfg = fresh_config(ANTHROPIC_KEY="sk-ant-abc")
    assert cfg.near_miss_env("ANTHROPIC_API_KEY") == ["ANTHROPIC_KEY"]
    assert cfg.ANTHROPIC_API_KEY == ""
    assert any("ANTHROPIC_KEY" in w and "ANTHROPIC_API_KEY" in w
               for w in cfg.config_warnings())


def test_differently_named_stripe_keys_are_not_near_misses(fresh_config):
    """STRIPE_API_KEY and STRIPE_PUBLISHABLE_KEY are different keys, not
    misspellings of the secret one — production has both, and calling either a
    near miss would send the operator to rename the wrong secret."""
    cfg = fresh_config(STRIPE_API_KEY="pk_live_abc",
                       STRIPE_PUBLISHABLE_KEY="pk_live_abc")
    assert cfg.near_miss_env("STRIPE_SECRET_KEY") == []
    assert cfg.config_warnings() == []


def test_no_warning_once_the_canonical_name_is_set(fresh_config):
    cfg = fresh_config(STRIPE_SECRET_KEY="sk_live_abc",
                       STRIPE_API_SECRET_KEY="sk_live_abc")
    assert cfg.near_miss_env("STRIPE_SECRET_KEY") == []
    assert cfg.config_warnings() == []


def test_tokens_missing_is_empty_under_productions_own_secret_names(fresh_config):
    """End to end, with exactly what `fly secrets list` shows on the app:
    nothing left standing between this config and taking money."""
    cfg = fresh_config(STRIPE_API_SECRET_KEY="sk_live_abc",
                       STRIPE_WEBHOOK_SECRET="whsec_abc",
                       TOKENS_ENABLED="1",
                       DATABASE_URL="postgresql://u:p@h/db")
    assert cfg.tokens_missing() == []
    assert cfg.tokens_enabled() is True
    assert cfg.stripe_live_mode() is True
    assert cfg.config_warnings() == []


def test_working_alias_does_not_warn(fresh_config):
    """DATABASE_URL is legitimately configured under a second name, so the
    feature works and there is nothing to report."""
    cfg = fresh_config(NEON_PRODUCTION_DATABASE_URL="postgresql://u:p@h/db")
    assert cfg.DATABASE_URL == "postgresql://u:p@h/db"
    assert cfg.config_warnings() == []


def test_flag_set_to_an_unrecognized_value_says_so(fresh_config):
    """TOKENS_ENABLED is deployed in production and still reads as off.
    'missing' and 'set to something I don't parse' are different bugs."""
    cfg = fresh_config(TOKENS_ENABLED="True_but_typo")
    assert cfg.TOKENS_ENABLED is False
    warning = [w for w in cfg.config_warnings() if "TOKENS_ENABLED" in w]
    assert warning and "True_but_typo" in warning[0]


def test_flag_set_properly_is_silent(fresh_config):
    assert fresh_config(TOKENS_ENABLED="true").config_warnings() == []
    assert fresh_config(TOKENS_ENABLED="1").config_warnings() == []
    assert fresh_config().config_warnings() == []

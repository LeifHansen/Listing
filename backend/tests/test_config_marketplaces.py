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


# --- Etsy's seller-app wall -------------------------------------------------
# Etsy grants a Seller app to exactly one account. Every other seller is
# refused on Etsy's OWN page ("Only the app owner may authorize a seller
# app"), after leaving this site, with nothing redirected back for the app to
# catch — so the only place to be kind about it is before the redirect.

def test_etsy_gate_is_off_until_an_owner_is_named(fresh_config):
    """No owner named = no way to tell the owner from anyone else, and
    guessing wrong locks the operator out of their own shop. Unconfigured
    therefore behaves exactly as it did before the gate existed."""
    cfg = fresh_config(ETSY_CLIENT_ID="key123",
                       ETSY_REDIRECT_URI="https://app.example/api/etsy/callback")
    assert cfg.etsy_oauth_ready()
    assert cfg.etsy_access_pending("owner@example.com") is False
    assert cfg.etsy_access_pending("anyone@example.com") is False
    assert cfg.etsy_access_pending("") is False


def test_etsy_owner_connects_while_everyone_else_waits(fresh_config):
    cfg = fresh_config(ETSY_OWNER_EMAILS="owner@example.com")
    assert cfg.etsy_gate_active() is True
    assert cfg.etsy_access_pending("owner@example.com") is False
    assert cfg.etsy_access_pending("seller@example.com") is True


def test_gate_active_is_false_in_both_do_nothing_states(fresh_config):
    """The provider checks this to skip a database round-trip per roster
    build, so it has to be false in exactly the cases where the answer can
    only be "not pending" anyway."""
    assert fresh_config().etsy_gate_active() is False          # no owner named
    assert fresh_config(ETSY_COMMERCIAL_ACCESS="true",         # nothing to gate
                        ETSY_OWNER_EMAILS="owner@example.com"
                        ).etsy_gate_active() is False


def test_etsy_owner_match_survives_case_and_padding(fresh_config):
    """The value is typed into a secrets dashboard by hand, once."""
    cfg = fresh_config(ETSY_OWNER_EMAILS=" Owner@Example.com , second@example.com ")
    assert cfg.ETSY_OWNER_EMAILS == ("owner@example.com", "second@example.com")
    assert cfg.etsy_access_pending("OWNER@EXAMPLE.COM ") is False
    assert cfg.etsy_access_pending("second@example.com") is False
    assert cfg.etsy_access_pending("third@example.com") is True


def test_etsy_unknown_caller_is_gated_not_waved_through(fresh_config):
    """"We don't know who this is" must not read as "this is the owner" — the
    roster is built for logged-out visitors too."""
    cfg = fresh_config(ETSY_OWNER_EMAILS="owner@example.com")
    assert cfg.etsy_access_pending("") is True
    assert cfg.etsy_access_pending(None) is True


def test_commercial_access_retires_the_gate_for_everyone(fresh_config):
    """The end state: Etsy approved the app, so the owner list stops
    mattering and nobody is held back."""
    cfg = fresh_config(ETSY_COMMERCIAL_ACCESS="true",
                       ETSY_OWNER_EMAILS="owner@example.com")
    assert cfg.etsy_access_pending("anyone@example.com") is False


def test_unparsed_commercial_access_flag_is_reported(fresh_config):
    """This flag fails CLOSED, which is the dangerous direction to be silent
    about: the operator believes they opened Etsy to every seller, and the app
    is still quietly showing them a pending-review card."""
    cfg = fresh_config(ETSY_COMMERCIAL_ACCESS="True_but_typo",
                       ETSY_OWNER_EMAILS="owner@example.com")
    assert cfg.ETSY_COMMERCIAL_ACCESS is False
    assert cfg.etsy_access_pending("seller@example.com") is True
    warning = [w for w in cfg.config_warnings() if "ETSY_COMMERCIAL_ACCESS" in w]
    assert warning and "True_but_typo" in warning[0]


def test_a_properly_set_commercial_access_flag_is_silent(fresh_config):
    assert fresh_config(ETSY_COMMERCIAL_ACCESS="true").config_warnings() == []
    assert fresh_config(ETSY_COMMERCIAL_ACCESS="1").config_warnings() == []


# --- which tier Etsy has us on ---------------------------------------------
# The wall above is Etsy's DEFAULT, not its only setting. Etsy tiers API
# access in three steps — seller (the keystring's owner alone), personal
# (approved for a handful of shops), commercial (everyone) — and the tier is
# what decides whether naming a second seller in ETSY_OWNER_EMAILS seats them
# or just moves where Etsy refuses them.

def test_the_tier_is_seller_until_etsy_says_otherwise(fresh_config):
    """Unset must read as the tier Etsy actually hands out by default, which
    is also the one that holds the most back. Reading it as anything else
    sends sellers to a consent screen that will refuse them."""
    cfg = fresh_config()
    assert cfg.etsy_access_tier() == "seller"
    assert cfg.etsy_seat_ceiling() == 1


def test_a_personal_approval_seats_a_roster_and_keeps_the_gate(fresh_config):
    """The day this branch was written for. Etsy approved the app for a
    handful of shops, so the named sellers can genuinely authorize now — and
    everyone else must still be held back, because Commercial Access is a
    separate grant that has not happened."""
    cfg = fresh_config(ETSY_ACCESS_TIER="personal",
                       ETSY_OWNER_EMAILS="owner@example.com,beta@example.com")
    assert cfg.etsy_access_tier() == "personal"
    assert cfg.etsy_seat_ceiling() == 4
    assert cfg.etsy_gate_active() is True
    assert cfg.etsy_access_pending("beta@example.com") is False
    assert cfg.etsy_access_pending("stranger@example.com") is True
    assert cfg.config_warnings() == []


def test_the_commercial_tier_retires_the_gate(fresh_config):
    """Same end state the older flag reaches, by the newer name."""
    cfg = fresh_config(ETSY_ACCESS_TIER="commercial",
                       ETSY_OWNER_EMAILS="owner@example.com")
    assert cfg.etsy_gate_active() is False
    assert cfg.etsy_access_pending("anyone@example.com") is False


def test_the_older_commercial_flag_still_names_the_tier(fresh_config):
    """ETSY_COMMERCIAL_ACCESS=true is documented and may already be set
    somewhere; it has to keep meaning what it said, and to win over a tier
    left behind at a lower value."""
    assert fresh_config(ETSY_COMMERCIAL_ACCESS="true").etsy_access_tier() == "commercial"
    assert fresh_config(ETSY_COMMERCIAL_ACCESS="true",
                        ETSY_ACCESS_TIER="personal"
                        ).etsy_access_tier() == "commercial"


def test_a_misspelled_tier_fails_closed_and_is_reported(fresh_config):
    """Fails closed, so the silent version of this is an operator who thinks
    their approved app is seating a beta while the app holds every named
    seller but one back."""
    cfg = fresh_config(ETSY_ACCESS_TIER="Personal-App",
                       ETSY_OWNER_EMAILS="owner@example.com")
    assert cfg.etsy_access_tier() == "seller"
    warning = [w for w in cfg.config_warnings() if "ETSY_ACCESS_TIER" in w]
    assert warning and "Personal-App" in warning[0]


# --- more sellers than Etsy seats ------------------------------------------
# The failure the approval creates. Naming a seller here does not seat them
# with Etsy: it waves them past THIS app's gate, and an unseated one is then
# refused on Etsy's own page, off-site, with nothing redirected back — the
# exact dead end the gate exists to prevent. From in here a named seller and a
# seated one look identical, so nobody finds out from the roster.

def test_a_roster_longer_than_etsy_seats_is_reported(fresh_config):
    cfg = fresh_config(ETSY_ACCESS_TIER="personal",
                       ETSY_OWNER_EMAILS=",".join(
                           f"s{i}@example.com" for i in range(5)))
    warning = [w for w in cfg.config_warnings() if "ETSY_OWNER_EMAILS" in w]
    assert warning and "5 sellers" in warning[0] and "seats 4" in warning[0]


def test_a_second_name_on_an_unapproved_app_is_reported(fresh_config):
    """A seller app is authorizable by the keystring's owner alone, so the
    second address does not open anything — it only moves the refusal from a
    card in this app to Etsy's error page."""
    cfg = fresh_config(ETSY_OWNER_EMAILS="owner@example.com,friend@example.com")
    warning = [w for w in cfg.config_warnings() if "ETSY_OWNER_EMAILS" in w]
    assert warning and "keystring's owner alone" in warning[0]


def test_commercial_access_has_no_ceiling_to_exceed(fresh_config):
    """Nothing is gated at that tier, so a long list is just a stale list —
    including one measured against a seat count left behind with it."""
    cfg = fresh_config(ETSY_ACCESS_TIER="commercial",
                       ETSY_OWNER_EMAILS=",".join(
                           f"s{i}@example.com" for i in range(9)))
    assert cfg.etsy_seat_ceiling() == 0
    assert cfg.config_warnings() == []
    assert fresh_config(ETSY_ACCESS_TIER="commercial", ETSY_APP_SEATS="4",
                        ETSY_OWNER_EMAILS=",".join(
                            f"s{i}@example.com" for i in range(9))
                        ).config_warnings() == []


def test_the_seat_count_follows_etsy_not_this_file(fresh_config):
    """The ceiling is Etsy's to move, and a deploy of this repo is the wrong
    thing to need when they do."""
    cfg = fresh_config(ETSY_ACCESS_TIER="personal", ETSY_APP_SEATS="6",
                       ETSY_OWNER_EMAILS=",".join(
                           f"s{i}@example.com" for i in range(5)))
    assert cfg.etsy_seat_ceiling() == 6
    assert cfg.config_warnings() == []


def test_an_unreadable_seat_override_keeps_a_ceiling(fresh_config):
    """Falling back to "no ceiling" would turn a typo into permission to add
    sellers Etsy has no seat for."""
    cfg = fresh_config(ETSY_ACCESS_TIER="personal", ETSY_APP_SEATS="four",
                       ETSY_OWNER_EMAILS=",".join(
                           f"s{i}@example.com" for i in range(5)))
    assert cfg.etsy_seat_ceiling() == 4
    assert [w for w in cfg.config_warnings() if "ETSY_APP_SEATS" in w]
    assert [w for w in cfg.config_warnings() if "ETSY_OWNER_EMAILS" in w]


def test_a_seat_count_that_is_not_a_count_cannot_stop_the_app_booting(
        fresh_config):
    """config_warnings() runs at import, so a value that raises while being
    read is not a wrong ceiling — it is a container that never binds a port
    and a deploy that fails its health poll. Reloading config under each of
    these IS the assertion: a raise here fails the test the way it would fail
    the boot. "2" superscript is the trap a string predicate walks into
    (str.isdigit() says yes, int() says no); a negative is the other one,
    since 0 already means "no ceiling" and clamping would hand a typo the
    answer that gates nobody."""
    for value in ("\u00b2", "-1", "4.0", "4 shops"):
        cfg = fresh_config(ETSY_ACCESS_TIER="personal", ETSY_APP_SEATS=value,
                           ETSY_OWNER_EMAILS="owner@example.com")
        assert cfg.etsy_seat_ceiling() == 4, value
        assert [w for w in cfg.config_warnings() if "ETSY_APP_SEATS" in w], value
    # Whitespace is the one shape that is NOT a typo to report: it strips to
    # empty, which is what an unset variable looks like from here, and every
    # other var in this file reads it that way too.
    blank = fresh_config(ETSY_ACCESS_TIER="personal", ETSY_APP_SEATS="   ",
                         ETSY_OWNER_EMAILS="owner@example.com")
    assert blank.etsy_seat_ceiling() == 4
    assert blank.config_warnings() == []


def test_a_readable_seat_count_is_silent_whatever_its_shape(fresh_config):
    """Padding and a plus sign are things a hand-typed number picks up; they
    parse, so they must not spend the operator's attention on a warning."""
    cfg = fresh_config(ETSY_ACCESS_TIER="personal", ETSY_APP_SEATS=" +6 ",
                       ETSY_OWNER_EMAILS="owner@example.com")
    assert cfg.etsy_seat_ceiling() == 6
    assert cfg.config_warnings() == []

"""What an anonymous caller is allowed to learn, and to be told.

Two consumer-facing leaks, of different kinds.

`GET /api/health` is unauthenticated and returned 26 operator-diagnostic keys:
the running commit SHA, every integration's readiness, the NAMES of unset
environment variables, the R2 bucket name and URL mode, free disk, which
Stripe mode the keys are in, config warnings naming near-miss secret names,
and — worst — the raw exception text from the database and object store, which
carries the Neon host and role on an auth failure and the R2 account id in its
endpoint. None of that helps a load balancer decide whether the app is alive,
which is the only thing a public health check is for.

The other is a publish that reports success without publishing. With no eBay
account connected, `POST /api/publish` answered `ok: true` with the rendered
Trading XML and a server filesystem path. To a seller "published" is a claim
about their listing being live on eBay; the listing was not created, and the
XML and the path are not something they can act on.

Neither is about hiding failure. The diagnostics still exist for whoever
operates the app; they just stop being anonymous. And the unconnected publish
still explains itself — it says to connect eBay, which is the actual next step.
"""
from __future__ import annotations

import pytest

# Importing backend.main pulls the whole app in. The `checks` job installs
# neither of these, so it skips this file; the smoke job's "API tests" step is
# where it runs, and that step fails on a skip so this can never quietly stop
# running.
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient


@pytest.fixture()
def api():
    from backend import main

    return TestClient(main.app)


# ------------------------------------------------------------- health

# Keys that describe the deployment rather than its liveness. Every one of
# these was public.
#
# "build" is deliberately NOT here. The deploy gate, deploy.sh and the
# health-watch alarm all poll it to prove production is running the commit
# that was shipped, and on a public repo a commit sha is not a secret --
# removing it would break three working safeguards to hide nothing.
OPERATOR_KEYS = (
    "ebay_missing", "objstore_missing", "objstore_bucket",
    "objstore_url_mode", "objstore_error", "disk_free_mb", "tokens_missing",
    "stripe_live_mode", "config_warnings", "db", "bg_engines", "ebay_env",
)


def test_public_health_says_only_whether_the_app_is_alive(api):
    """A load balancer needs one bit, the UI needs a few capability flags,
    and the deploy gate needs the build sha. Everything else was for an
    operator and was being handed to anyone who asked."""
    body = api.get("/api/health").json()

    assert body.get("ok") is True
    leaked = sorted(k for k in OPERATOR_KEYS if k in body)
    assert not leaked, f"public health still exposes: {leaked}"


def test_public_health_keeps_what_the_deploy_gate_and_ui_depend_on(api):
    """The other half of the trade. Trimming this endpoint must not break the
    three tools that poll it (deploy.yml, deploy.sh, health-watch.yml) or the
    UI banners that read the capability flags."""
    body = api.get("/api/health").json()

    assert body.get("build")
    for flag in ("anthropic_configured", "ebay_configured",
                 "taxonomy_configured"):
        assert flag in body, flag


def test_public_health_never_carries_raw_failure_text(api):
    """objstore_error and db.error embed the R2 endpoint (the account id) and
    the Neon host and role. Those are infrastructure identifiers, published
    to anyone, on a route with no rate limit."""
    text = api.get("/api/health").text

    for fragment in ("neon.tech", "r2.cloudflarestorage", "postgresql://",
                     "AccessDenied", "password"):
        assert fragment.lower() not in text.lower(), fragment


def test_the_diagnostics_still_exist_behind_admin_auth(api, monkeypatch):
    """The point is to move them, not delete them: whoever operates the app
    still needs to see which env var is missing."""
    from backend import config

    monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret")
    body = api.get("/api/admin/diagnostics",
                   headers={"x-admin-token": "s3cret"}).json()

    assert body["build"] is not None
    assert "objstore_missing" in body
    assert "db" in body


def test_diagnostics_report_the_etsy_tier_without_the_roster(api, monkeypatch):
    """Which tier Etsy has the app on is the operator's answer to "why can't
    this seller connect", and the seat ceiling is what says whether adding one
    more will work or just move the refusal to Etsy's page. The roster itself
    is people's email addresses, so it is reported as a count: an endpoint
    that exists to explain a configuration is not a place to hand out the
    beta list."""
    from backend import config

    monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret")
    monkeypatch.setattr(config, "ETSY_OWNER_EMAILS",
                        ("owner@example.com", "beta@example.com"))
    body = api.get("/api/admin/diagnostics",
                   headers={"x-admin-token": "s3cret"}).json()

    assert body["etsy_access_tier"] in config.ETSY_ACCESS_TIERS
    assert body["etsy_seats"] == config.etsy_seat_ceiling()
    assert body["etsy_roster"] == 2
    assert "example.com" not in str(body)


def test_diagnostics_refuse_a_wrong_or_missing_token(api, monkeypatch):
    from backend import config

    monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret")

    assert api.get("/api/admin/diagnostics").status_code == 401
    assert api.get("/api/admin/diagnostics",
                   headers={"x-admin-token": "wrong"}).status_code == 401


def test_diagnostics_are_closed_when_no_admin_token_is_configured(api,
                                                                  monkeypatch):
    """Fail closed. An unset token must not mean "no check required" — that
    is how the endpoint would end up public again on a deploy that forgot
    to set it."""
    from backend import config

    monkeypatch.setattr(config, "ADMIN_TOKEN", "")

    assert api.get("/api/admin/diagnostics").status_code in (401, 404, 503)
    assert api.get("/api/admin/diagnostics",
                   headers={"x-admin-token": ""}).status_code in (401, 404, 503)


# ------------------------------------------------- publishing nothing

def test_an_unconnected_live_publish_does_not_report_success(monkeypatch):
    """"Published" is a claim about the listing being live on eBay. With no
    account connected nothing was created, so ok must be false."""
    from backend.marketplaces import ebay_provider
    from backend.marketplaces.base import PublishContext
    from backend.models import Listing

    monkeypatch.setattr(ebay_provider.config, "EBAY_ENV", "production")
    outcome = ebay_provider.EbayProvider()._dry_run(
        PublishContext(session_id="s1", listing=Listing(title="A lamp"),
                       mode="live", base_url="http://x", uid="u1",
                       prev_record=None))

    assert outcome.ok is False
    assert "connect" in (outcome.message or "").lower()


def test_it_never_returns_the_xml_or_a_server_path_in_production(monkeypatch):
    """The XML and the export path are implementation detail a seller cannot
    act on, and the path describes the server's filesystem."""
    from backend.marketplaces import ebay_provider
    from backend.marketplaces.base import PublishContext
    from backend.models import Listing

    monkeypatch.setattr(ebay_provider.config, "EBAY_ENV", "production")
    outcome = ebay_provider.EbayProvider()._dry_run(
        PublishContext(session_id="s1", listing=Listing(title="A lamp"),
                       mode="live", base_url="http://x", uid="u1",
                       prev_record=None))

    assert "payload" not in outcome.raw
    assert "export_path" not in outcome.raw


def test_the_payload_preview_survives_outside_production(monkeypatch):
    """It is a real development tool — the only way to see what a publish
    would send without an account. It just stops being a production answer."""
    from backend.marketplaces import ebay_provider
    from backend.marketplaces.base import PublishContext
    from backend.models import Listing

    monkeypatch.setattr(ebay_provider.config, "EBAY_ENV", "sandbox")
    outcome = ebay_provider.EbayProvider()._dry_run(
        PublishContext(session_id="s1", listing=Listing(title="A lamp"),
                       mode="live", base_url="http://x", uid="u1",
                       prev_record=None))

    assert outcome.raw.get("payload")
    assert outcome.raw["payload"]["call"].startswith("Add")

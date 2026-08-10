"""R2 configuration resolution — the exact shape of the August 2026 incident:
credentials deployed, storage silently dormant because optional-in-spirit
variables were required in code."""
from __future__ import annotations

_CREDS = {
    "R2_ACCOUNT_ID": "abc123",
    "R2_ACCESS_KEY_ID": "key",
    "R2_SECRET_ACCESS_KEY": "secret",
}


def test_unconfigured_names_every_missing_credential(fresh_config):
    cfg = fresh_config()
    assert cfg.r2_missing() == [
        "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]
    assert not cfg.r2_configured()


def test_credentials_alone_are_enough(fresh_config):
    """The incident class: bucket + public URL must NOT be required."""
    cfg = fresh_config(**_CREDS)
    assert cfg.r2_missing() == []
    assert cfg.r2_configured()
    assert cfg.R2_BUCKET == "thryftshop2"  # the .env.example canonical name
    assert not cfg.r2_public_urls()          # presigned mode until a URL is set


def test_partial_credentials_name_the_gap(fresh_config):
    cfg = fresh_config(R2_ACCOUNT_ID="abc123")
    assert cfg.r2_missing() == ["R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]


def test_placeholder_values_count_as_unset(fresh_config):
    """'<your account id>' pasted verbatim is a recurring Fly-secrets mistake;
    it must degrade to unconfigured, not get sent to Cloudflare."""
    cfg = fresh_config(**{**_CREDS, "R2_ACCOUNT_ID": "<your account id>"})
    assert cfg.r2_missing() == ["R2_ACCOUNT_ID"]
    assert not cfg.r2_configured()


def test_explicit_bucket_and_public_url_win(fresh_config):
    cfg = fresh_config(**_CREDS, R2_BUCKET="custom",
                       R2_PUBLIC_BASE_URL="https://img.example.com/")
    assert cfg.R2_BUCKET == "custom"
    assert cfg.r2_public_urls()
    assert cfg.R2_PUBLIC_BASE_URL == "https://img.example.com"  # slash stripped

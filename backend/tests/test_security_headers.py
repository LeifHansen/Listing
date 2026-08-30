"""The response headers a browser needs in order to defend the seller.

None of these were being sent. Each closes a different door:

  - Content-Security-Policy stops an injected payload from loading a remote
    script or calling eval, which is the usual next step after an injection;
  - frame-ancestors / X-Frame-Options stop the app being framed by another
    site, which matters because it has publish and delete buttons;
  - X-Content-Type-Options stops a browser guessing that an uploaded file is
    HTML and running it — this app takes file uploads from the public;
  - Referrer-Policy stops session ids in URLs leaking to third parties in the
    Referer header (they are already in every /media URL);
  - Permissions-Policy turns off capabilities the app does not use.

The CSP is deliberately not maximal — see the comment on _CSP. What these
tests pin is that it exists, that it forbids the things it must, and that it
still permits what the app genuinely loads, so a future tightening cannot
quietly break the fonts or the eBay-hosted photos.
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


def test_every_response_carries_the_headers(api):
    headers = api.get("/api/health").headers

    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=(self)" in headers["permissions-policy"]
    assert "microphone=()" in headers["permissions-policy"]


def test_the_csp_forbids_the_things_it_must(api):
    csp = api.get("/api/health").headers["content-security-policy"]

    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    # A wide-open script-src would make the whole header decorative.
    assert "script-src 'self' 'unsafe-inline'" in csp
    assert "script-src *" not in csp


def test_the_csp_still_allows_what_the_app_actually_loads(api):
    """A policy that breaks the app on deploy is worse than a partial one.
    index.html pulls Google Fonts, listings show eBay-hosted photos, and the
    photo editor works on canvas blobs before anything is uploaded."""
    csp = api.get("/api/health").headers["content-security-policy"]

    assert "https://fonts.googleapis.com" in csp
    assert "https://fonts.gstatic.com" in csp
    assert "img-src 'self' https: data: blob:" in csp


def test_an_error_response_is_protected_too(api):
    """Headers set per-route would miss exactly the responses an attacker
    steers the app into."""
    headers = api.get("/api/listings/definitely-not-a-real-listing").headers

    assert headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in headers


def test_hsts_is_sent_only_over_https(api):
    """Sending it on a plain-HTTP local run pins a developer's browser to
    https://localhost, which nothing here serves."""
    plain = api.get("/api/health")
    assert "strict-transport-security" not in plain.headers

    behind_proxy = api.get("/api/health",
                           headers={"x-forwarded-proto": "https"})
    assert "max-age=31536000" in behind_proxy.headers[
        "strict-transport-security"]

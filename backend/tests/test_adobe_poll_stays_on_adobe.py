"""A job's status URL comes back in a response body — check it before polling.

`_submit` POSTs an async job and reads the poll URL out of the answer:

    href = str(resp.json()["_links"]["self"]["href"])

`_wait` then polls that href with `_headers()`, which carries the Adobe IMS
bearer token and the client id. Nothing checked where the href pointed. Adobe
is the one who wrote it, so this is not the request-forgery the Etsy photo
fetch was — but a response shape we did not choose deciding where a live
credential gets sent is the wrong default for a secret. An open redirect on
their side, a compromised or spoofed response, or simply the wrong base URL in
an operator's environment all end with our token in someone else's log.

The right constraint is exact and costs nothing: the poll URL must be on the
origin we submitted to. That is `config.ADOBE_IMAGE_API_BASE`, so an operator
pointing the app at a different Adobe endpoint still works — what cannot
happen is the answer moving us off it.
"""
from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("PIL")
pytest.importorskip("boto3")

from backend import config  # noqa: E402
from backend.services import adobe  # noqa: E402


class _Resp:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload if payload is not None else {"status": "succeeded"}
        self.status_code = status_code
        self.text = "{}"

    def json(self):
        return self._payload


@pytest.fixture
def polled(monkeypatch):
    """Every URL _wait actually sent the Adobe credential to."""
    seen: list[str] = []

    def _get(url, *a, **kw):
        seen.append(url)
        return _Resp()

    monkeypatch.setattr(adobe, "_access_token", lambda: "tok")
    monkeypatch.setattr(httpx, "get", _get)
    return seen


@pytest.mark.parametrize("href", [
    "https://evil.test/status/1",                  # somewhere else entirely
    "https://image.adobe.io.evil.test/status/1",   # suffix trick
    "https://evilimage.adobe.io.attacker/1",       # lookalike
    "http://image.adobe.io/status/1",              # downgraded to plaintext
    "https://169.254.169.254/latest/meta-data/",   # internal
    "/lrService/status/1",                         # relative — no origin at all
    "",                                            # nothing
])
def test_the_token_only_goes_to_the_endpoint_we_submitted_to(polled, href):
    with pytest.raises(adobe.AdobeError):
        adobe._wait(href)
    assert polled == [], f"credential was sent to {href}"


def test_a_real_adobe_status_url_still_polls(polled):
    href = f"{config.ADOBE_IMAGE_API_BASE}/lrService/status/abc123"
    adobe._wait(href)          # succeeded on the first poll
    assert polled == [href]


def test_an_operator_configured_endpoint_is_honoured(monkeypatch, polled):
    """The check is against the configured base, not a hardcoded hostname."""
    monkeypatch.setattr(config, "ADOBE_IMAGE_API_BASE", "https://image-eu.adobe.io")
    href = "https://image-eu.adobe.io/lrService/status/abc123"
    adobe._wait(href)
    assert polled == [href]

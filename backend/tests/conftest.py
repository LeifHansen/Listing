"""Shared fixtures: an isolated, reloadable config.

config.py has import-time side effects (reads .env, creates DATA_DIR trees,
persists a generated SECRET_KEY), so tests never rely on whatever environment
the module was first imported under. The fixture pins a scratch DATA_DIR,
scrubs every variable under test, reloads config, and resets objstore's
process-wide client/latch state.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Point DATA_DIR somewhere disposable BEFORE backend.config is first imported:
# importing it creates the data tree and persists a generated SECRET_KEY, and
# without this a bare `pytest` litters the repo (and CI's checkout) with a
# data/ directory and a warning on every run.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="thryft-tests-"))
os.environ.setdefault("SECRET_KEY", "test-secret")

from backend import config, objstore  # noqa: E402


_SCRUBBED = (
    "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET", "R2_PUBLIC_BASE_URL",
    # Billing: these decide whether the paid tier reports itself as ready, so
    # a value inherited from the developer's own environment would make the
    # readiness tests pass or fail for reasons unrelated to the code.
    "TOKENS_ENABLED", "DATABASE_URL", "NEON_PRODUCTION_DATABASE_URL",
    "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
    # The near-miss names config.config_warnings() looks for. Scrubbed for the
    # same reason as the canonical ones: inherited from a developer's shell
    # they would make the warning fire (or not) for reasons unrelated to code.
    "STRIPE_API_SECRET_KEY", "STRIPE_API_WEBHOOK_SECRET",
    "ANTHROPIC_API_KEY", "ANTHROPIC_KEY", "API_SECRET_KEY",
    # Etsy's seller-app gate: which sellers may reach Etsy's consent screen.
    # Inherited from a developer's shell it would gate (or un-gate) the tests
    # for reasons unrelated to the code.
    "ETSY_COMMERCIAL_ACCESS", "ETSY_OWNER_EMAILS",
)


@pytest.fixture
def fresh_config(monkeypatch, tmp_path):
    """Reload backend.config under a controlled environment. Returns a
    function tests call with the env they want, e.g.:

        cfg = fresh_config(R2_ACCOUNT_ID="abc", ...)
    """
    def _reload(**env: str):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("SECRET_KEY", "test-secret")
        for name in _SCRUBBED:
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        importlib.reload(config)
        _reset_objstore()
        return config

    yield _reload
    # Leave the process the way we found it for any non-reloading test.
    importlib.reload(config)
    _reset_objstore()


def _reset_objstore() -> None:
    objstore._client = None
    objstore._error = None
    objstore._error_at = 0.0


@pytest.fixture
def dbmod(monkeypatch, tmp_path):
    """A fresh db module bound to a scratch SQLite file.

    Shared: the deletion-cascade and notification suites both need a real
    database with real schema, and each carried its own byte-identical copy
    of this until one of them would inevitably drift.
    """
    from backend import config, db

    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    importlib.reload(db)
    monkeypatch.setattr(db.config, "DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    return db


# --- no test may talk to the internet ---------------------------------------
#
# Three tests in test_ebay_error_240.py were making REAL HTTPS requests to
# https://api.sandbox.ebay.com/sell/account/v1/privilege on every run, with the
# literal token "tok". They inject the payments lookup and stop there, and
# `publish_block_issues` asks a second API (selling privileges) that they left
# un-injected; `fetch_privileges` swallows its own failure, so the tests passed
# while the branch they exist to cover never ran.
#
# The cost is not only slowness. A suite that reaches a third party depends on
# the network to be correct, sends unauthenticated requests to somebody else's
# service from CI on every push, and — the part that matters here — reports a
# green result for a code path it never executed. That is the same failure as
# the four flaky safety tests this branch already fixed, one layer down.
#
# httpx.HTTPTransport is where a request stops being a mock. Sockets are the
# wrong layer: with HTTPS_PROXY set every request connects to 127.0.0.1, so a
# socket guard sees nothing. Starlette's TestClient uses its own ASGI transport
# and never reaches this one, so in-process API tests are unaffected.
#
# Imported here rather than at the top of the file because the `cutout` CI job
# installs Pillow/NumPy/SciPy and nothing else: a hard import would break the
# image suite at COLLECTION, which is a worse failure than the one this
# prevents. Without httpx there is nothing that could make a request anyway.
try:
    import httpx
except ImportError:  # pragma: no cover - the image-only CI job
    httpx = None


def _refuse_outbound(self, request):
    raise AssertionError(
        f"a test made a real network request: {request.method} {request.url}\n"
        "Stub the client (monkeypatch httpx.post/get on the module under "
        "test) or inject the lookup. If a test genuinely needs the network, "
        "mark it @pytest.mark.allow_network — and say why.")


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if httpx is None or request.node.get_closest_marker("allow_network"):
        return
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _refuse_outbound)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_network: this test really does need the internet (say why)")

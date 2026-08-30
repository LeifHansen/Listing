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
    # Limited-release eBay scope flags. Inherited from a developer's shell
    # they would decide what EBAY_OAUTH_SCOPES contains, so the scope tests
    # would pass or fail for reasons unrelated to the code.
    "EBAY_LOGISTICS_ENABLED", "EBAY_MESSAGING_ENABLED",
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

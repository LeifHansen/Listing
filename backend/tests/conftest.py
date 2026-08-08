"""Shared fixtures: an isolated, reloadable config.

config.py has import-time side effects (reads .env, creates DATA_DIR trees,
persists a generated SECRET_KEY), so tests never rely on whatever environment
the module was first imported under. The fixture pins a scratch DATA_DIR,
scrubs every variable under test, reloads config, and resets objstore's
process-wide client/latch state.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend import config, objstore  # noqa: E402


_SCRUBBED = (
    "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET", "R2_PUBLIC_BASE_URL",
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

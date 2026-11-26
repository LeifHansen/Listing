"""A typo in the AI timeout costs its default, not the whole app.

AI_TIMEOUT_SECONDS and AI_MAX_RETRIES bound both vision backends. Gemini's
module read them through config.env_float, which survives "120s"; Claude's
read the same two variables with a bare float()/int() at import time -- so
the one setting meant to stop a wedged request from pinning a worker could,
spelled the way people spell durations, stop the app from starting at all.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("httpx")

ROOT = Path(__file__).resolve().parents[2]


def test_both_backends_boot_on_a_misspelled_timeout(tmp_path):
    env = {**os.environ, "DATA_DIR": str(tmp_path),
           "AI_TIMEOUT_SECONDS": "120s", "AI_MAX_RETRIES": "two"}
    out = subprocess.run(
        [sys.executable, "-c",
         "from backend.services import claude_ai, google_ai\n"
         "print(claude_ai._AI_TIMEOUT_SECONDS, claude_ai._AI_MAX_RETRIES,\n"
         "      google_ai._TIMEOUT_SECONDS, google_ai._MAX_RETRIES)"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    # The app's log shares stdout and says what it fell back to; the values
    # are the last line.
    assert out.stdout.strip().splitlines()[-1].split() == ["120.0", "2",
                                                          "120.0", "2"]

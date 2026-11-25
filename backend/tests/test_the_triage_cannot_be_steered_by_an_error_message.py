"""The daily triage prints what production said without obeying it, and checks
a captured warning against the tree as well as a crash.

Two things about the rows the triage reads.

A message is text from production, some of it typed into a browser. The
script printed it after `::notice::` in a GitHub Actions step, where a line
that starts with `::` is a COMMAND to the runner -- so a message carrying a
newline could add a mask, stop command processing, or write its own error
annotation. Everything printed from a row is one line now.

And a row recorded from a log line names its module the way logging does --
the bare file stem, "images" -- where a crash names it dotted
("backend.services.images"). The "recorded by an older build" check only
understood the dotted form, so it never fired for the captured warnings that
are most of the feed. The fingerprints are left alone (changing them would
make every known error look new); the triage resolves the stem instead.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def triage():
    path = REPO_ROOT / ".github" / "scripts" / "triage_errors.py"
    spec = importlib.util.spec_from_file_location("triage_errors", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(**fields) -> dict:
    return {"fingerprint": "abcd1234abcd1234", "severity": "high",
            "exc_type": "ValueError", "message": "boom", "count": 9,
            "traceback": "Traceback ...", "module": "backend.services.images",
            "func": "optimize", **fields}


def test_a_message_with_a_newline_cannot_issue_a_runner_command(
        triage, tmp_path, capsys):
    feed = tmp_path / "feed.json"
    feed.write_text(json.dumps({"errors": [_row(
        message="bad photo\n::add-mask::secret\r\n::error::pwned")]}))
    assert triage.main(str(feed), str(tmp_path / "none.json"),
                       str(tmp_path / "work.json"), str(REPO_ROOT)) == 0
    for line in capsys.readouterr().out.splitlines():
        assert not line.startswith(("::add-mask::", "::error::pwned")), line


def test_a_bare_module_stem_that_is_in_the_tree_is_not_called_stale(triage):
    assert triage.why_not(_row(module="images"), {}, str(REPO_ROOT)) is None


def test_a_bare_module_stem_no_file_answers_to_is_an_older_build(triage):
    reason = triage.why_not(_row(module="a_module_that_was_deleted"), {},
                            str(REPO_ROOT))
    assert reason and "older build" in reason


def test_a_stem_that_is_not_a_name_is_not_looked_for(triage):
    """It came from production; it never becomes a path walked on disk."""
    assert triage.why_not(_row(module="../../etc"), {}, str(REPO_ROOT)) is None

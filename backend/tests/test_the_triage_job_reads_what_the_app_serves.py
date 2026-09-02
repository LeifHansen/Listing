"""The daily triage job and the feed it reads have to agree.

This is the twin of test_the_health_alarm_reads_what_the_app_serves.py, and it
exists because of what that file records. `/api/health` was trimmed, the alarm
went on reading keys that were no longer there, and for four days it failed on
every schedule against a production that was entirely healthy. The endpoint had
tests. The script had none. Nothing knew the two were connected.

`.github/scripts/triage_errors.py` reads fingerprint, severity, count,
traceback, exc_type, module, resolved_at — every one of them a key somebody
could rename in db.py without ever opening the workflow. So this asserts the
JOIN: the real script, run against the real body from the real route, and then
the branches it is supposed to fire on.

The exit-code contract gets its own tests, because it is the opposite of the
health alarm's and easy to "fix" into agreement with it. Exit 1 means "I could
not look". Finding bugs is exit 0 — a job that goes red every morning that
production has a bug is a notification nobody reads by the second week, which
is the same alarm fatigue arriving by a different route.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

# Importing backend.main pulls in the whole app. The `checks` job installs
# none of these, so this file skips there; it runs in the smoke job's "API
# tests" step, which fails on a skip so it can never quietly stop running.
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import config, main  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN = "the-feed-token"


def _triage():
    """The workflow's own script, imported from where the workflow runs it.

    By path, deliberately: `.github/scripts/` is not a package and never will
    be, and re-implementing the rules here would defeat the entire purpose of
    the file — the test has to exercise the code that actually runs.
    """
    path = REPO_ROOT / ".github" / "scripts" / "triage_errors.py"
    spec = importlib.util.spec_from_file_location("triage_errors", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def triage():
    return _triage()


@pytest.fixture()
def feed(dbmod, monkeypatch):
    """The real route's real body, with one real recorded failure in it."""
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(config, "ERROR_FEED_TOKEN", TOKEN)
    client = TestClient(main.app)

    def _serve(**over):
        dbmod.record_error_event(**{
            "fingerprint": "aa11bb22cc33dd44", "severity": "high",
            "exc_type": "ValueError", "message": "a genuine bug",
            "traceback": "Traceback (most recent call last):\n  ...",
            "module": "backend.services.recommender", "func": "recommend_for",
            "lineno": 63, "route": "/api/insights", "method": "GET",
            **over})
        return client.get("/api/ops/error-feed",
                          headers={"x-error-feed-token": TOKEN}).json()

    return _serve


def test_the_script_finds_every_key_it_reads_in_the_real_payload(triage, feed,
                                                                 tmp_path):
    """The regression that shipped once already: the reader kept reading keys
    the writer had stopped sending, and nothing said so."""
    body = feed()
    path = tmp_path / "feed.json"
    path.write_text(json.dumps(body))

    picked = triage.candidates(triage.load(str(path), "feed"), {},
                               str(REPO_ROOT))

    assert len(picked) == 1, "the real payload should yield the real bug"
    item = triage.work_item(picked[0])
    # Every field the agent is handed came from somewhere, not from a default.
    assert item["fingerprint"] == "aa11bb22cc33dd44"
    assert item["exc_type"] == "ValueError"
    assert item["where"] == "backend.services.recommender.recommend_for"
    assert item["route"] == "/api/insights"
    assert item["count"] == 1
    assert item["traceback"].startswith("Traceback")
    assert item["last_seen"], "without this the agent cannot tell stale from live"


def test_a_clean_feed_finds_nothing_and_still_succeeds(triage, tmp_path):
    """The ordinary day. It must be green, and it must open nothing."""
    path = tmp_path / "feed.json"
    path.write_text(json.dumps({"errors": [], "sink": {"dropped": 0}}))
    out = tmp_path / "work.json"

    code = triage.main(str(path), str(tmp_path / "none.json"), str(out),
                       str(REPO_ROOT))

    assert code == 0
    assert json.loads(out.read_text())["candidates"] == []


def test_a_feed_it_cannot_read_is_the_one_thing_that_fails(triage, tmp_path):
    """"I could not look" and "I looked and it was clean" must never be the
    same outcome. This is the entire reason the job has an exit code."""
    missing = tmp_path / "nope.json"
    assert triage.main(str(missing), str(missing), str(tmp_path / "w.json"),
                       str(REPO_ROOT)) == 1

    edge_page = tmp_path / "edge.json"
    edge_page.write_text("<html>502 Bad Gateway</html>")
    assert triage.main(str(edge_page), str(missing), str(tmp_path / "w.json"),
                       str(REPO_ROOT)) == 1


def test_finding_bugs_is_a_green_day(triage, feed, tmp_path):
    """The opposite of health-watch.yml, and deliberately so."""
    path = tmp_path / "feed.json"
    path.write_text(json.dumps(feed()))
    out = tmp_path / "work.json"

    code = triage.main(str(path), str(tmp_path / "none.json"), str(out),
                       str(REPO_ROOT))

    assert code == 0
    assert len(json.loads(out.read_text())["candidates"]) == 1


def _row(**over) -> dict:
    base = {"fingerprint": "ff00", "severity": "high", "count": 9,
            "traceback": "Traceback...", "exc_type": "TypeError",
            "module": "backend.main", "func": "x", "message": "m"}
    base.update(over)
    return base


class TestTheBar:
    """What earns a pull request, and what does not."""

    def _why(self, triage, **over):
        return triage.why_not(_row(**over), {}, str(REPO_ROOT))

    def test_a_real_traceback_qualifies_the_first_time(self, triage):
        assert self._why(triage, count=1) is None

    def test_a_lone_sentence_with_no_stack_does_not(self, triage):
        assert "no traceback" in self._why(triage, count=1, traceback="",
                                           severity="medium")

    def test_the_same_sentence_often_enough_does(self, triage):
        assert self._why(triage, count=triage.MIN_COUNT, traceback="",
                         severity="medium") is None

    def test_a_third_party_being_down_is_not_a_bug_here(self, triage):
        """No change to this repository fixes eBay being unreachable."""
        assert "third party" in self._why(triage, exc_type="ConnectError")

    def test_a_storage_outage_is_not_a_bug_here_either(self, triage):
        assert "third party" in self._why(triage, exc_type="StorageUnavailable")

    def test_something_graded_low_is_left_alone(self, triage):
        assert "graded low" in self._why(triage, severity="low")

    def test_a_bug_from_a_build_that_no_longer_exists_is_skipped(self, triage):
        """Acting on a stale fact is worse than not acting: the code the
        traceback names is not the code in the tree."""
        assert "not in the tree" in self._why(triage,
                                              module="backend.deleted_module")

    def test_a_browser_crash_is_judged_without_a_python_path(self, triage):
        assert self._why(triage, module="browser", kind="frontend") is None

    def test_a_bug_that_already_has_a_fix_is_not_proposed_twice(self, triage):
        assert "already has a fix" in self._why(
            triage, resolved_at="2026-09-01T00:00:00Z", fix_pr="pull/9")

    def test_a_silenced_fingerprint_stays_silent(self, triage):
        why = triage.why_not(_row(), {"ff00": "known, wontfix"},
                             str(REPO_ROOT))
        assert "silenced" in why


def test_only_a_handful_are_proposed_in_one_morning(triage):
    """A bound on how much review one run can create, not a claim about how
    many bugs exist — the rest keep their place and come back tomorrow."""
    rows = [_row(fingerprint=f"fp{i:04d}") for i in range(25)]
    picked = triage.candidates({"errors": rows}, {}, str(REPO_ROOT))

    assert len(picked) == triage.MAX_CANDIDATES


def test_the_worst_ones_go_first(triage):
    rows = [_row(fingerprint="low1", severity="medium", count=500),
            _row(fingerprint="hi1", severity="high", count=2),
            _row(fingerprint="hi2", severity="high", count=80)]
    picked = triage.candidates({"errors": rows}, {}, str(REPO_ROOT))

    assert [r["fingerprint"] for r in picked] == ["hi2", "hi1", "low1"]


def test_a_dropping_sink_is_reported_rather_than_hidden(triage, tmp_path,
                                                        capsys):
    """A feed that lost rows must not read as a quiet day."""
    path = tmp_path / "feed.json"
    path.write_text(json.dumps({"errors": [], "sink": {"dropped": 12}}))

    triage.main(str(path), str(tmp_path / "n.json"),
                str(tmp_path / "w.json"), str(REPO_ROOT))

    assert "::warning::" in capsys.readouterr().out


def test_what_the_agent_is_handed_is_a_subset_not_the_row(triage):
    """The agent reads text that came from production, some of it from a
    browser. It gets what it needs to find the bug and nothing else."""
    item = triage.work_item(_row(user_id="u-123", reference="a1b2c3d4",
                                 data={"stack": "..."}))

    assert "user_id" not in item
    assert "reference" not in item

"""The Health watch alarm and the payload it reads have to agree.

They stopped agreeing and nobody noticed for four days.

`/api/health` was trimmed back to liveness plus the UI's capability flags,
because anonymous and unrate-limited it was publishing 26 operator keys: the
R2 bucket, the NAMES of unset environment variables, free disk, the Stripe
mode, and raw exception text carrying the Neon host and role and the R2
account id. That was right, and test_public_surface.py holds it in place.

What went with it was the alarm. `.github/scripts/check_health.py` reads
disk_free_mb, the object-store state and the database from whatever body it is
handed, and the workflow went on handing it `/api/health`. Every key it wanted
was gone, so from 2026-08-31 the alarm failed on EVERY two-hourly schedule --
"disk_free_mb missing or not a number: None", "object storage not configured;
missing=None" -- against a production with 493MB free, R2 healthy and the
database connected. Four consecutive failures, each one an email and a phone
push, none of them true.

That is the worst state for an alarm to be in. A silent alarm fails only when
something breaks; an always-red one is already failing, cannot report the real
disk or R2 or database problem it exists for, and spends its operator's
attention until they stop reading it.

Nothing here could have caught that: the endpoint had its tests, the script
had none, and no test knew they were connected. So this file asserts the join
-- the alarm is run against the body the app really serves, from the real
route -- and then the branches it is supposed to fire on.
"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

# Importing backend.main pulls the whole app in. The `checks` job installs
# neither of these, so it skips this file; the smoke job's "API tests" step is
# where it runs, and that step fails on a skip so this can never quietly stop
# running.
pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import config, main, objstore, storage  # noqa: E402

# The threshold health-watch.yml passes. Kept as a literal rather than parsed
# out of the YAML: the point of the tests below is the SHAPE of the exchange,
# and a test that reads the workflow to find its own expected value can agree
# with a workflow that is wrong.
MIN_FREE_MB = 400


def _checker():
    """The workflow's own script, imported from where the workflow runs it.

    By path, deliberately. `.github/scripts/` is not a package and never will
    be, and copying the logic into the test would defeat the entire purpose of
    this file -- the copy would keep passing while the script it was copied
    from went on reading keys that no longer exist.
    """
    path = Path(__file__).resolve().parents[2] / ".github/scripts/check_health.py"
    assert path.exists(), f"the alarm's script has moved: {path}"
    spec = importlib.util.spec_from_file_location("check_health", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def checker():
    return _checker()


@pytest.fixture()
def client():
    return TestClient(main.app)


@pytest.fixture()
def healthy(monkeypatch):
    """A machine with room, a reachable database and a working bucket.

    The test process has none of those by default -- no R2 credentials, no
    database -- so without this the "silent when healthy" cases below would be
    asserting against a payload no production ever returns.
    """
    monkeypatch.setattr(storage, "disk_free_bytes", lambda: 9_000 * 1_000_000)
    monkeypatch.setattr(main.db, "db_status",
                        lambda: {"configured": True, "connected": True})
    monkeypatch.setattr(config, "r2_configured", lambda: True)
    monkeypatch.setattr(objstore, "last_error", lambda: None)


# ------------------------------------------------------- the join

def test_the_alarm_reads_keys_the_real_payload_actually_has(client, checker,
                                                            healthy):
    """The regression. Run the workflow's script against the workflow's route.

    Against `/api/health` -- where it was pointed -- every one of these comes
    back missing, which is exactly the state production was alarming in.
    """
    body = client.get("/api/ready").json()

    missing = [p for p in checker.problems(body, MIN_FREE_MB) if "missing" in p]
    assert not missing, f"the alarm asks for keys /api/ready does not serve: {missing}"


def test_a_healthy_machine_raises_nothing_at_all(client, checker, healthy):
    """Not merely "no missing keys": a green production has to be silent, or
    the alarm is back to crying wolf on every schedule."""
    body = client.get("/api/ready").json()

    assert checker.problems(body, MIN_FREE_MB) == []


def test_the_liveness_payload_is_no_longer_a_source_for_it(client, checker):
    """The other half of the same fact, stated from the other side: pointing
    the alarm back at /api/health must not quietly start working again."""
    body = client.get("/api/health").json()

    assert [p for p in checker.problems(body, MIN_FREE_MB) if "missing" in p]


# ------------------------------------------------ what it fires on

def test_a_volume_running_out_is_reported_before_it_is_empty(checker, client,
                                                             healthy,
                                                             monkeypatch):
    """The warning threshold sits ABOVE the 503 floor on purpose -- it fires
    while reclaim is still coping, not after it has gone desperate."""
    monkeypatch.setattr(storage, "disk_free_bytes", lambda: 300 * 1_000_000)
    body = client.get("/api/ready").json()

    assert body["ready"] is True, "still serving -- this is a warning, not an outage"
    assert any("300MB free" in p for p in checker.problems(body, MIN_FREE_MB))


def test_a_failing_readiness_check_is_named_not_just_counted(checker, client,
                                                             healthy,
                                                             monkeypatch):
    """The readiness step can only report a status code. Naming which check
    went false is the whole reason this second step exists."""
    monkeypatch.setattr(storage, "writable", lambda: False)
    body = client.get("/api/ready").json()

    assert any("storage_writable" in p for p in checker.problems(body, MIN_FREE_MB))


def test_a_degraded_bucket_is_reported_and_says_where_the_reason_is(checker):
    """R2 latching off is invisible from everywhere else: photos quietly stay
    on the volume, the volume cannot be reclaimed, and publishes start failing
    later for what looks like an unrelated reason."""
    found = checker.problems(
        {"ready": True, "checks": {"storage_writable": True}, "disk_free_mb": 9000,
         "object_storage": {"configured": True, "degraded": True}}, MIN_FREE_MB)

    assert any("degraded" in p for p in found)
    assert any("diagnostics" in p for p in found), (
        "the reason names the R2 account, so the alarm has to point at the "
        "endpoint that may carry it rather than carrying it itself")


def test_the_latch_is_never_reported_as_a_missing_credential(checker):
    """The old pair reported both through one boolean, so a bucket that broke
    ten minutes ago read as "not configured; missing=[]" and sent the operator
    hunting for variables that were all present."""
    found = checker.problems(
        {"ready": True, "checks": {"storage_writable": True}, "disk_free_mb": 9000,
         "object_storage": {"configured": True, "degraded": True}}, MIN_FREE_MB)

    assert not any("not configured" in p for p in found)


def test_a_bucket_that_was_never_set_up_is_its_own_message(checker):
    found = checker.problems(
        {"ready": True, "checks": {"storage_writable": True}, "disk_free_mb": 9000,
         "object_storage": {"configured": False, "degraded": False}}, MIN_FREE_MB)

    assert any("not configured" in p for p in found)


def test_an_edge_error_page_reads_as_down_not_as_a_traceback(checker, tmp_path):
    """Fly serves HTML when the machine is wedged -- the single most important
    case this alarm covers."""
    page = tmp_path / "ready.json"
    page.write_text("<html><body>error</body></html>")

    assert checker.load(str(page)) is None


# ------------------------------------- what it must not have cost

def test_the_object_store_state_says_nothing_a_secret_is_named_in(client,
                                                                  monkeypatch):
    """/api/ready is public, so the state added for the alarm has to be
    booleans and nothing else. The raw reason carries the R2 account id in its
    endpoint, which is precisely why it moved behind the admin token."""
    monkeypatch.setattr(objstore, "_error",
                        "AccessDenied from https://acct123.r2.cloudflarestorage.com "
                        "for bucket thryft-images")
    monkeypatch.setattr(objstore, "_error_at", time.time())

    res = client.get("/api/ready")

    assert res.json()["object_storage"]["degraded"] is True
    for fragment in ("r2.cloudflarestorage", "thryft-images", "AccessDenied",
                     "R2_ACCOUNT_ID", "neon.tech"):
        assert fragment.lower() not in res.text.lower(), fragment


def test_a_broken_bucket_does_not_take_the_machine_out_of_service(client,
                                                                  healthy,
                                                                  monkeypatch):
    """Photos still land on the volume and the app is still serving. Turning a
    Cloudflare blip into a 503 would pull the only machine out of the load
    balancer -- a degradation escalated into an outage."""
    monkeypatch.setattr(objstore, "last_error", lambda: "bucket unreachable")

    res = client.get("/api/ready")

    assert res.status_code == 200
    assert res.json()["ready"] is True
    assert res.json()["object_storage"]["degraded"] is True

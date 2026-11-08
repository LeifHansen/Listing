"""Readiness.

Two failure modes that used to look identical from the outside -- "the server
is fine but full" and "the server is broken" -- and both showed up as a
spinner that never resolved.
"""
from __future__ import annotations


import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main, storage  # noqa: E402
from backend.services import images  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(main.app)


def test_ready_reports_the_things_that_stop_photo_work(client):
    body = client.get("/api/ready").json()
    assert set(body["checks"]) == {"storage_writable", "disk_space", "database"}
    assert "image_engine" in body


def test_ready_answers_503_when_it_cannot_do_the_work(client, monkeypatch):
    """The point of a readiness probe: a machine that can't write photos has
    to SAY so, in a status code, not report 200 and fail the next upload."""
    monkeypatch.setattr(storage, "writable", lambda: False)
    res = client.get("/api/ready")
    assert res.status_code == 503
    assert res.json()["ready"] is False
    assert res.json()["checks"]["storage_writable"] is False


def test_low_disk_is_not_ready(client, monkeypatch):
    monkeypatch.setattr(storage, "disk_free_bytes", lambda: 5 * 1_000_000)
    assert client.get("/api/ready").status_code == 503


def test_a_configured_but_unreachable_database_is_not_ready(client, monkeypatch):
    monkeypatch.setattr(main.db, "db_status",
                        lambda: {"configured": True, "connected": False})
    assert client.get("/api/ready").status_code == 503


def test_no_database_configured_is_a_valid_setup(client, monkeypatch):
    """Filesystem-only is a supported deployment, not a fault."""
    monkeypatch.setattr(main.db, "db_status",
                        lambda: {"configured": False, "connected": False})
    assert client.get("/api/ready").status_code == 200


def test_health_stays_up_even_when_not_ready(client, monkeypatch):
    """Liveness and readiness are different questions. A full disk means stop
    sending work here; it does not mean restart the process."""
    monkeypatch.setattr(storage, "writable", lambda: False)
    assert client.get("/api/health").status_code == 200


# --- the busy path ----------------------------------------------------------
def test_the_engine_state_a_probe_reads_is_cheap_and_complete(monkeypatch):
    """Which engine cuts backgrounds, whether it has a key, and whether that
    key only makes watermarked previews. Config only: no network and no
    model, so it stays as cheap as the rest of the payload. It is here because
    a missing key shows up nowhere else -- every photo is just kept as shot."""
    monkeypatch.setattr(images.cutout_api.config, "PHOTOROOM_API_KEY", "")
    monkeypatch.setattr(images.cutout_api.config, "PHOTOROOM_SANDBOX", False)
    assert images.engine_state() == {
        "engine": "photoroom", "ready": False, "sandbox": False}
    monkeypatch.setattr(images.cutout_api.config, "PHOTOROOM_API_KEY", "k")
    assert images.engine_state()["ready"] is True


def test_ready_names_the_engine(client):
    assert client.get("/api/ready").json()["image_engine"]["engine"] == "photoroom"



def test_a_full_or_unreadable_volume_counts_as_low():
    """disk_free_bytes() reports 0 for BOTH a genuinely full volume and a stat
    it could not take -- it swallows the error and returns 0. Neither is
    evidence of room, so both have to escalate.

    The guard used to be `bool(free) and free < limit`, meaning to say "if we
    know the free space". Its actual effect was to read 0 as "no reason to
    hurry" and switch aggressive reclaim off at the one moment it exists for.
    """
    aggressive, _trim, delay = main._reclaim_plan(0)
    assert aggressive is True
    assert delay == main._RECLAIM_INTERVAL_LOW


def test_a_low_volume_is_revisited_sooner():
    """The docstring has always promised "sooner when the volume is running
    low"; the loop slept three hours either way, so a volume that filled
    mid-batch stayed broken until the next pass came round on its own."""
    aggressive, _trim, delay = main._reclaim_plan(main._LOW_DISK_BYTES - 1)
    assert aggressive is True
    assert delay == main._RECLAIM_INTERVAL_LOW
    assert delay < main._RECLAIM_INTERVAL


def test_a_healthy_volume_keeps_the_slow_pass():
    """The other half of the trade: room to spare must NOT shorten the TTLs.
    Aggressive mode drops originals after 15 minutes, so leaving it on costs
    every subsequent edit a round trip to R2 for bytes that were local."""
    aggressive, trim, delay = main._reclaim_plan(main._TRIM_DISK_BYTES + 1)
    assert aggressive is False
    assert trim is False
    assert delay == main._RECLAIM_INTERVAL


def test_the_band_where_the_alarm_pages_is_trimmed_not_ignored():
    """Between the app's own emergency line and the health-watch alarm's
    there was nothing: the alarm paged every two hours for three days from
    2026-08-31 (281-393 MB free on the 1 GB volume) while the daemon kept
    week-long TTLs and a three-hour nap, because 250 MB had not been
    reached. In that band it now trims, on the short interval, and still
    stops short of the desperate TTLs that make every fresh edit go to R2."""
    assert main._TRIM_DISK_BYTES > main._LOW_DISK_BYTES
    aggressive, trim, delay = main._reclaim_plan(main._LOW_DISK_BYTES + 1)
    assert aggressive is False
    assert trim is True
    assert delay == main._RECLAIM_INTERVAL_LOW
    # Below the emergency line it is aggressive, never both.
    aggressive, trim, _delay = main._reclaim_plan(main._LOW_DISK_BYTES - 1)
    assert (aggressive, trim) == (True, False)


def test_trim_reclaims_sooner_than_the_slow_pass_and_later_than_desperate(
        monkeypatch):
    """The TTLs each mode hands the pruners, pinned in order: trim must be
    strictly between the slow pass and the emergency on every axis, or the
    band it exists for either changes nothing or costs what aggressive costs."""
    seen: dict[str, list[int]] = {}

    def _spy(name):
        def _f(ttl, *a, **k):
            seen.setdefault(name, []).append(int(ttl))
            return 0
        return _f

    monkeypatch.setattr(main, "_sweep_orphans", lambda: None)
    monkeypatch.setattr(main, "_offload_to_r2", _spy("offload"))
    for fn in ("prune_originals", "prune_history", "prune_exports"):
        monkeypatch.setattr(main.storage, fn, _spy(fn))

    main.reclaim_space()
    main.reclaim_space(trim=True)
    main.reclaim_space(aggressive=True)
    for name, (normal, trim, aggressive) in seen.items():
        assert aggressive < trim < normal, (name, normal, trim, aggressive)
    # Originals are what a resumed batch and a fresh edit still need: trim
    # keeps them for hours, not the emergency's fifteen minutes.
    assert seen["prune_originals"][1] >= 3600


def test_api_answers_are_never_cached(client):
    """Account-state answers (/api/ebay/status above all) went out with NO
    cache directive, leaving heuristic caching to the browser and anything in
    between -- and a stale copy of "which eBay account is connected" had a
    seller debugging an account switch against yesterday's answer."""
    res = client.get("/api/health")
    assert res.headers.get("Cache-Control") == "no-store"

"""Readiness and the busy path.

Two failure modes that used to look identical from the outside -- "the server
is fine but full" and "the server is broken" -- and both showed up as a
spinner that never resolved.
"""
from __future__ import annotations

import threading

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
def test_a_full_inference_queue_gives_up_instead_of_hanging(monkeypatch):
    """The 'appears to hang' bug. One inference runs at a time, and the wait
    for that slot used to be unbounded -- so a studio cutout queued behind a
    forty-photo batch sat there alive and idle, forever.

    An interactive caller passes its own (short) deadline, because there a
    prompt "busy, try again" is the useful answer."""
    from PIL import Image

    images._INFER_LOCK.acquire()
    try:
        with pytest.raises(images.CutoutBusy):
            images._alpha_mask(Image.new("RGB", (64, 64), (200, 200, 200)),
                               wait=0.05)
    finally:
        images._INFER_LOCK.release()


def test_a_batch_photo_queues_for_the_model_instead_of_giving_up(monkeypatch):
    """The other half of that trade, and the more expensive one to get wrong.

    A photo in a background batch has nobody watching it, so giving up buys
    nothing: there is no retry, the photo is just SAVED WITH ITS BACKGROUND
    STILL ON. One deadline used to serve both callers at 25s -- shorter than a
    single inference on a loaded box -- so in a batch running two photos at a
    time, the queued one was guaranteed to time out and keep its background.
    The batch deadline has to outlast a real queue."""
    from PIL import Image

    assert images.BATCH_INFER_WAIT_SECONDS >= 120, (
        "the batch deadline has to outlast a slow inference, or queued photos "
        "silently keep their backgrounds")
    assert images.BATCH_INFER_WAIT_SECONDS > images.INFER_WAIT_SECONDS

    # With the slot held, a batch caller (no explicit wait) must still be
    # waiting when the interactive deadline would already have given up.
    monkeypatch.setattr(images, "BATCH_INFER_WAIT_SECONDS", 30)
    monkeypatch.setattr(images, "INFER_WAIT_SECONDS", 0.05)
    settled = threading.Event()
    outcome = []

    def _queued_photo() -> None:
        try:
            images._alpha_mask(Image.new("RGB", (64, 64), (200, 200, 200)))
            outcome.append("got-the-slot")
        except images.CutoutBusy:
            outcome.append("gave-up")
        except Exception:  # noqa: BLE001 - got the slot, then found no rembg
            outcome.append("got-the-slot")
        settled.set()

    worker = threading.Thread(target=_queued_photo, daemon=True)
    images._INFER_LOCK.acquire()
    try:
        worker.start()
        # Well past the interactive deadline, comfortably short of the batch
        # one. Neither outcome yet -- still queued -- is the whole point.
        assert not settled.wait(0.5), f"batch photo settled early: {outcome}"
    finally:
        images._INFER_LOCK.release()
    # Wait on the deadline under test, and JOIN. Both matter.
    #
    # The old budget was a flat 5 seconds, which is a bet on the environment
    # rather than on the behaviour: where rembg is absent (CI installs the
    # server without it) _alpha_mask raises the moment it has the slot, but
    # where it is present the winning thread pays for a model load and a real
    # inference first. On a cold, shared box that overran, and the failure it
    # produced was `outcome == []` -- which reads as "the photo never got the
    # slot", the exact opposite of what happened.
    #
    # Worse, the thread was left running while holding _INFER_LOCK, so
    # engine_state()["busy"] stayed True for the rest of the session and the
    # NEXT test failed too, pointing at a probe that was fine. One slow
    # machine, two red tests, neither naming the cause. Joining bounds that:
    # _alpha_mask releases the lock in a finally, so a joined worker cannot
    # leak it into another test.
    #
    # Nothing is loosened by waiting longer. Giving up is not a slow success:
    # it raises CutoutBusy at BATCH_INFER_WAIT_SECONDS and appends
    # "gave-up", which still fails the assertion below.
    worker.join(images.BATCH_INFER_WAIT_SECONDS + 5)
    assert not worker.is_alive(), (
        "the queued photo never settled within the batch deadline")
    assert outcome == ["got-the-slot"]


def test_busy_is_retryable_not_a_crash():
    """CutoutBusy has to be its own type: main maps it to 503 + Retry-After,
    while an engine's own complaint is a 422 about the photo. Collapsing them
    tells the seller their photo is bad when the machine was merely busy."""
    assert issubclass(images.CutoutBusy, RuntimeError)
    assert not issubclass(images.CutoutBusy, ValueError)


def test_the_engine_state_a_probe_reads_is_cheap_and_complete():
    state = images.engine_state()
    assert set(state) == {"model", "loaded", "busy", "last_inference_seconds",
                          "model_load_seconds"}
    assert state["busy"] is False


def test_a_slow_model_load_is_not_reported_as_a_slow_inference(monkeypatch,
                                                                caplog):
    """The first call pays for importing onnxruntime and building the session
    around a 176MB file; that is a load, and it happened once per boot. It
    was timed under the same clock as the inference behind it, so every
    deploy's warm-up reported itself as a ~107s inference on /api/ready,
    fired the slow-inference warning into the error feed, and stayed as the
    "last inference" until a real photo came through -- which on a quiet day
    is never. Nothing could then say what an inference actually costs."""
    import sys
    import time
    import types

    from PIL import Image

    fake = types.ModuleType("rembg")

    def new_session(model):
        time.sleep(0.25)          # the load, deliberately over the slow bar
        return object()

    def remove(img, session=None, only_mask=False):
        return Image.new("L", img.size, 255)

    fake.new_session = new_session
    fake.remove = remove
    monkeypatch.setitem(sys.modules, "rembg", fake)
    monkeypatch.setattr(images, "_rembg_session", None)
    monkeypatch.setattr(images, "_model_ready", False)
    monkeypatch.setattr(images, "_last_infer_seconds", 0.0)
    monkeypatch.setattr(images, "_model_load_seconds", 0.0)
    monkeypatch.setattr(images, "INFER_SLOW_SECONDS", 0.1)

    with caplog.at_level("WARNING", logger="thryft"):
        images._alpha_mask(Image.new("RGB", (64, 64), (200, 200, 200)))

    state = images.engine_state()
    assert state["loaded"] is True
    assert state["model_load_seconds"] >= 0.25
    assert state["last_inference_seconds"] < 0.1, (
        "the load was counted as the inference")
    assert not [r for r in caplog.records if "inference took" in r.getMessage()], (
        "a one-time load was reported as a pathological inference")


# --- the housekeeping daemon's decision ------------------------------------
#
# reclaim_space() had tests for what it frees; nothing covered when the daemon
# decides to run it, which is where the defect was.


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

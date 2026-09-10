"""A bulk batch drafts several items at once.

Drafting one item is five to nine Anthropic calls and a handful of eBay
lookups, and the machine spends nearly all of that waiting on somebody else's
server. Doing it strictly one item at a time left the box idle for most of a
batch: forty items took the better part of an hour while nothing was running.

What that has to survive is everything the serial loop got right, so these are
tests about the guarantees rather than about the speed. An item is charged for
once. An item that fails takes only itself down. The queue the seller watches
stays in the order they shot the photos, however the drafts happen to land.
And BULK_DRAFT_WORKERS=1 is still exactly the old behaviour, because that is
the off switch.
"""
from __future__ import annotations

import threading

import pytest

pytest.importorskip("PIL")
pytest.importorskip("fastapi")

from PIL import Image  # noqa: E402

from backend import config, main, storage  # noqa: E402
from backend.models import IdentifyResult, Listing  # noqa: E402
from backend.services import jobstore  # noqa: E402

ITEMS = 4


def _photos(dir_, n, prefix="img"):
    dir_.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (60, 60), (240, 240, 240)).save(
            dir_ / f"{prefix}_{i:03d}.jpg", "JPEG")
    return [f"{prefix}_{i:03d}.jpg" for i in range(n)]


@pytest.fixture(autouse=True)
def _own_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    jobstore.reset()
    yield
    for job_id in list(jobstore._JOBS):
        jobstore.update(job_id, done=True)
    jobstore.reset()


@pytest.fixture
def pile():
    """A batch that has finished grouping: ITEMS items of two photos each,
    still on the volume, nothing drafted."""
    staging = storage.new_session_id()
    names = _photos(storage.optimized_dir(staging), ITEMS * 2)
    groups = [{"name": f"item {g}", "indices": [2 * g, 2 * g + 1]}
              for g in range(ITEMS)]
    return staging, names, groups


@pytest.fixture
def quiet_pipeline(monkeypatch):
    """Everything around the per-item AI stubbed out. `identify` is left to
    the test, because when it answers is the whole point here."""
    for name in ("_resolve_category", "_assign_store_category",
                 "_enrich_listing", "_drop_answered_missing_info",
                 "_lookup_artwork", "_research_draft", "_price_against_comps"):
        monkeypatch.setattr(main, name, lambda *a, **k: None)
    monkeypatch.setattr(main, "_apply_listing_defaults",
                        lambda listing, uid, prefs=None: listing)
    monkeypatch.setattr(main, "_load_prefs", lambda uid: {})
    monkeypatch.setattr(main, "_auto_promote_enabled", lambda uid: False)
    monkeypatch.setattr(main.objstore, "upload_optimized", lambda *a, **k: None)
    monkeypatch.setattr(main.db, "upsert_listing", lambda *a, **k: True)
    monkeypatch.setattr(main.claude_ai, "warm_identify_cache", lambda: True)


@pytest.fixture
def money(monkeypatch):
    """Every charge and every refund this batch makes, in order."""
    spent, refunded = [], []
    lock = threading.Lock()

    def _spend(uid, kind, units=1):
        with lock:
            spent.append(kind)
            return {"ok": True, "entry_id": f"e{len(spent)}", "user_id": uid}

    def _refund(receipt, units=None):
        with lock:
            refunded.append((receipt or {}).get("entry_id"))
        return True

    monkeypatch.setattr(main.tokens, "enabled", lambda: True)
    monkeypatch.setattr(main.tokens, "spend", _spend)
    monkeypatch.setattr(main.tokens, "refund", _refund)
    return spent, refunded


def _run(staging, names, groups, uid="owner", job="job-1"):
    jobstore.register(job, {"phase": "identifying", "done": False,
                            "items": []}, uid=uid)
    main._run_bulk_job(job, staging, False, uid, resumed=True,
                       resume_from={"names": names, "groups": groups,
                                    "done": [], "precharged": {},
                                    "remaining": list(range(len(groups)))})
    return jobstore.snapshot(job, uid)


# --- the point of the change ------------------------------------------------

def test_several_items_really_are_drafted_at_once(pile, quiet_pipeline,
                                                  money, monkeypatch):
    """A barrier as wide as the batch, and no item may pass it alone. Drafted
    one at a time, the first item would sit there until it timed out and come
    back as an error row — so a batch of clean drafts IS the proof that all
    four were in flight together."""
    staging, names, groups = pile
    monkeypatch.setattr(main, "BULK_DRAFT_WORKERS", ITEMS)
    gate = threading.Barrier(ITEMS, timeout=10)

    def fake_identify(paths, image_names, strategy="", **kw):
        gate.wait()
        return IdentifyResult(listing=Listing(title="drafted", price=10,
                                              images=list(image_names)))

    monkeypatch.setattr(main.claude_ai, "identify", fake_identify)

    snap = _run(staging, names, groups)

    assert snap["done"] is True and snap.get("error") is None
    assert [it["status"] for it in snap["items"]] == ["draft"] * ITEMS, (
        "an item that waited at the barrier alone would be an error row")


def test_the_warm_up_runs_once_before_the_fan_out(pile, quiet_pipeline, money,
                                                  monkeypatch):
    """The identify prompt's static half is prompt-cached, and a cache entry is
    only readable once the request that wrote it starts answering — so workers
    started together would every one of them miss it and each write its own
    copy. One prefill first, and only when there is a fan-out to warm."""
    staging, names, groups = pile
    warmed = []
    monkeypatch.setattr(main.claude_ai, "warm_identify_cache",
                        lambda: warmed.append(1) or True)
    monkeypatch.setattr(main.claude_ai, "identify",
                        lambda paths, n, strategy="", **kw: IdentifyResult(
                            listing=Listing(title="drafted", price=10,
                                            images=list(n))))

    monkeypatch.setattr(main, "BULK_DRAFT_WORKERS", 3)
    _run(staging, names, groups)
    assert warmed == [1], "warmed once for the whole batch, not once per item"

    warmed.clear()
    monkeypatch.setattr(main, "BULK_DRAFT_WORKERS", 1)
    _run(staging, names, groups, job="job-2")
    assert warmed == [], (
        "one at a time, the first item warms the cache by drafting — a "
        "separate warm-up would be a second write for nothing")


# --- what it must not break -------------------------------------------------

def test_the_queue_stays_in_the_order_the_seller_shot_it(pile, quiet_pipeline,
                                                         money, monkeypatch):
    """Which item finishes first is an accident of how many photos it had and
    how long the AI took. The queue is the seller's pile, so it is ordered by
    the group, and `current` counts what has landed."""
    staging, names, groups = pile
    monkeypatch.setattr(main, "BULK_DRAFT_WORKERS", ITEMS)

    def fake_identify(paths, image_names, strategy="", **kw):
        return IdentifyResult(listing=Listing(
            title=f"drafted {paths[0].parent.parent.name}", price=10,
            images=list(image_names)))

    monkeypatch.setattr(main.claude_ai, "identify", fake_identify)

    snap = _run(staging, names, groups)

    assert [it["name"] for it in snap["items"]] == [
        f"item {g}" for g in range(ITEMS)]
    assert snap["current"] == ITEMS
    # The thumb on each row belongs to that row's own session.
    for it in snap["items"]:
        assert it["thumb"].startswith(f"/media/{it['session_id']}/")


def test_one_item_that_fails_takes_only_itself_down(pile, quiet_pipeline,
                                                    money, monkeypatch):
    """A rate limit on one item is not a reason to lose the other three — and
    the AI that never ran is paid back."""
    staging, names, groups = pile
    spent, refunded = money
    monkeypatch.setattr(main, "BULK_DRAFT_WORKERS", 3)
    seen = []
    lock = threading.Lock()

    def fake_identify(paths, image_names, strategy="", **kw):
        with lock:
            seen.append(paths[0].parent.parent.name)
            nth = len(seen)
        if nth == 2:
            raise RuntimeError("rate limited")
        return IdentifyResult(listing=Listing(title="drafted", price=10,
                                              images=list(image_names)))

    monkeypatch.setattr(main.claude_ai, "identify", fake_identify)

    snap = _run(staging, names, groups)

    assert snap["done"] is True and snap.get("error") is None
    statuses = [it["status"] for it in snap["items"]]
    assert statuses.count("draft") == ITEMS - 1 and statuses.count("error") == 1
    assert len(snap["items"]) == ITEMS, "the failed item still gets a row"
    failed = next(it for it in snap["items"] if it["status"] == "error")
    assert "rate limited" in failed["error"]
    # Charged once each; the one that failed got its charge back.
    assert spent == ["identify"] * ITEMS
    assert len(refunded) == 1


def test_one_item_is_charged_for_once(pile, quiet_pipeline, money, monkeypatch):
    staging, names, groups = pile
    spent, refunded = money
    monkeypatch.setattr(main, "BULK_DRAFT_WORKERS", 3)
    monkeypatch.setattr(main.claude_ai, "identify",
                        lambda paths, n, strategy="", **kw: IdentifyResult(
                            listing=Listing(title="drafted", price=10,
                                            images=list(n))))

    snap = _run(staging, names, groups)

    assert spent == ["identify"] * ITEMS and refunded == []
    sids = [it["session_id"] for it in snap["items"]]
    assert len(set(sids)) == ITEMS, "each item drafted in a session of its own"
    for sid in sids:
        assert storage.load_listing(sid)["title"] == "drafted"


def test_the_off_switch_drafts_strictly_in_order(pile, quiet_pipeline, money,
                                                 monkeypatch):
    """BULK_DRAFT_WORKERS=1 is the rollback, and it has to be the old
    behaviour rather than a pool that happens to have one worker."""
    staging, names, groups = pile
    monkeypatch.setattr(main, "BULK_DRAFT_WORKERS", 1)
    started = []

    def fake_identify(paths, image_names, strategy="", **kw):
        started.append(paths[0].parent.parent.name)
        return IdentifyResult(listing=Listing(title="drafted", price=10,
                                              images=list(image_names)))

    monkeypatch.setattr(main.claude_ai, "identify", fake_identify)

    snap = _run(staging, names, groups)

    assert started == [it["session_id"] for it in snap["items"]], (
        "drafted in group order, one at a time")
    assert snap["current"] == ITEMS


def test_a_stop_lets_what_is_paid_for_finish_and_starts_nothing_new(
        pile, quiet_pipeline, money, monkeypatch):
    """Stopping is checked between items, never inside one: an item that has
    started has already been charged for, so it is finished and saved. What
    the stop buys is that nothing NEW is picked up."""
    staging, names, groups = pile
    spent, refunded = money
    monkeypatch.setattr(main, "BULK_DRAFT_WORKERS", 2)
    jobstore.register("job-stop", {"phase": "identifying", "done": False,
                                   "items": []}, uid="owner")
    drafted = threading.Event()

    def fake_identify(paths, image_names, strategy="", **kw):
        drafted.set()
        return IdentifyResult(listing=Listing(title="drafted", price=10,
                                              images=list(image_names)))

    def stop_after_the_first(job_id):
        # The seller's cancel, landing once at least one item is under way.
        return drafted.is_set()

    monkeypatch.setattr(main.claude_ai, "identify", fake_identify)
    monkeypatch.setattr(main.jobstore, "cancel_requested", stop_after_the_first)

    main._run_bulk_job("job-stop", staging, False, "owner", resumed=True,
                       resume_from={"names": names, "groups": groups,
                                    "done": [], "precharged": {},
                                    "remaining": list(range(ITEMS))})

    snap = jobstore.snapshot("job-stop", "owner")
    assert snap["done"] is True and snap["cancelled"] is True
    assert snap["phase"] == "stopped"
    landed = snap["items"]
    assert 0 < len(landed) < ITEMS, "some finished, the rest never started"
    assert all(it["status"] == "draft" for it in landed)
    # Only the items that actually ran were ever charged for.
    assert len(spent) == len(landed) and refunded == []
    for it in landed:
        assert storage.load_listing(it["session_id"])["title"] == "drafted"


def test_the_queue_is_built_by_group_whatever_order_the_drafts_land_in():
    """The ordering itself, without the timing: rows go in as groups 2, 0, 3, 1
    and come out 0, 1, 2, 3. jobstore.update merges whole FIELDS, so this is
    also what stops two workers losing each other's row."""
    jobstore.register("job-order", {"phase": "identifying", "done": False,
                                    "items": []}, uid="owner")
    progress = main._BulkProgress("job-order")
    for gi in (2, 0, 3, 1):
        progress.mark_started(gi, f"sid-{gi}")
        progress.mark_done(gi, {"session_id": f"sid-{gi}", "name": f"item {gi}",
                                "status": "draft", "error": None,
                                "title": f"t{gi}"})

    snap = jobstore.snapshot("job-order", "owner")
    assert [it["name"] for it in snap["items"]] == [f"item {g}" for g in range(4)]
    assert snap["current"] == 4, "counts what landed, not the highest index"
    mirrored = jobstore._JOBS["job-order"]
    assert [d["gi"] for d in mirrored["_done"]] == [0, 1, 2, 3]
    assert mirrored["_inflight"] == [], "nothing left in flight"


def test_an_item_in_flight_is_written_down_before_its_ai_is_charged_for():
    """The order matters: a restart between the two finishes that item in the
    session that already holds its photos, without charging a second time."""
    jobstore.register("job-flight", {"phase": "identifying", "done": False,
                                     "items": []}, uid="owner")
    progress = main._BulkProgress("job-flight")
    progress.mark_started(1, "sid-1")
    progress.mark_started(2, "sid-2")

    mirrored = jobstore._JOBS["job-flight"]
    assert mirrored["_inflight"] == [{"gi": 1, "session_id": "sid-1"},
                                     {"gi": 2, "session_id": "sid-2"}]
    assert mirrored["_done"] == [] and mirrored["current"] == 0

    progress.mark_done(1, {"session_id": "sid-1", "name": "item 1",
                           "status": "draft", "error": None, "title": "t"})
    mirrored = jobstore._JOBS["job-flight"]
    assert mirrored["_inflight"] == [{"gi": 2, "session_id": "sid-2"}]
    assert [d["gi"] for d in mirrored["_done"]] == [1]

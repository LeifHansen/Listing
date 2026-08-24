"""Background-job status, and what survives a restart.

The failure these cover: a bulk batch's status lived only in the worker
process, so a machine that went away mid-batch (an OOM-kill during background
removal, a deploy) left the client polling an id that 404s forever — a progress
bar frozen on the photo the batch died at, with no result and no error. The
mirror has to outlive the process that wrote it, and has to come back saying
what happened.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from backend import config
from backend.services import jobstore


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A jobstore with an empty data root of its own. The in-memory dict is
    process-wide, so it is cleared on both sides of the test."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    jobstore.reset()
    yield jobstore
    jobstore.reset()


def _mirror_files(tmp_path):
    return sorted(p.name for p in (tmp_path / "jobs").glob("*.json"))


# --------------------------------------------------------- the live store

def test_a_registered_job_reads_back(store):
    store.register("abc123", {"phase": "optimizing", "done": False})
    assert store.snapshot("abc123")["phase"] == "optimizing"


def test_an_unknown_job_is_none(store):
    """The endpoint turns None into a 404 — it must not invent a status."""
    assert store.snapshot("nope") is None


def test_updates_merge_into_the_job(store):
    store.register("abc123", {"phase": "optimizing", "current": 0})
    store.update("abc123", current=37, total_photos=38)
    snap = store.snapshot("abc123")
    assert (snap["current"], snap["total_photos"], snap["phase"]) == (37, 38, "optimizing")


def test_updating_an_unknown_job_is_a_no_op(store):
    store.update("nope", current=1)  # must not raise, must not create a job
    assert store.snapshot("nope") is None


def test_a_job_is_private_to_its_owner(store):
    """Status carries drafted titles and prices."""
    store.register("abc123", {"phase": "identifying"}, uid="owner")
    assert store.snapshot("abc123", "owner") is not None
    assert store.snapshot("abc123", "someone-else") is None
    assert store.snapshot("abc123", None) is None


def test_an_anonymous_job_is_readable_by_the_id_holder(store):
    """Logged-out sessions work the same way everywhere else in the app."""
    store.register("abc123", {"phase": "identifying"}, uid=None)
    assert store.snapshot("abc123", None) is not None
    assert store.snapshot("abc123", "anyone") is not None


def test_the_snapshot_never_leaks_internal_bookkeeping(store):
    store.register("abc123", {"phase": "done"}, uid="owner")
    assert "_uid" not in store.snapshot("abc123", "owner")


def test_the_snapshot_is_a_copy(store):
    """A caller mutating its reply must not corrupt the live job."""
    store.register("abc123", {"items": [{"title": "a"}]})
    store.snapshot("abc123")["items"].append({"title": "b"})
    assert len(store.snapshot("abc123")["items"]) == 1


# ------------------------------------------------------------- eviction

def test_eviction_spares_a_job_that_is_still_running(store):
    """The old rule dropped the oldest entry, and a bulk batch IS the oldest
    entry while 200 quick identifies run past it — evicting the one job whose
    poller is actually watching."""
    store.register("batch", {"phase": "optimizing", "done": False})
    for i in range(jobstore.JOBS_MAX + 5):
        store.register(f"quick{i}", {"kind": "identify", "done": True})
    assert store.snapshot("batch") is not None


# --------------------------------------------------------- the disk mirror

def test_a_job_is_mirrored_to_disk(store, tmp_path):
    store.register("abc123", {"phase": "optimizing"})
    store.update("abc123", current=37, total_photos=38)
    record = json.loads((tmp_path / "jobs" / "abc123.json").read_text())
    assert (record["current"], record["total_photos"]) == (37, 38)


def test_the_mirror_holds_status_only(store, tmp_path):
    """Never the drafted items: they are already listing rows, and a 250-photo
    batch would rewrite megabytes of them on every progress tick."""
    store.register("abc123", {"phase": "identifying"})
    store.update("abc123", items=[{"session_id": "s1", "listing": {"title": "x"}}],
                 result={"listing": {"title": "x"}}, current=1)
    record = json.loads((tmp_path / "jobs" / "abc123.json").read_text())
    assert "items" not in record and "result" not in record
    assert record["current"] == 1


def test_a_mirror_failure_never_breaks_the_job(store, monkeypatch, tmp_path):
    """A full or read-only volume must cost the batch its bookkeeping, not its
    work. Here the data root is a FILE, so every write under it fails."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("")
    monkeypatch.setattr(config, "DATA_DIR", blocked)

    store.register("abc123", {"phase": "optimizing"})
    store.update("abc123", current=1)

    assert store.snapshot("abc123")["current"] == 1
    assert store.adopt_mirrors() == []  # and reading it back is just as quiet


# ------------------------------------------- what comes back from a restart

def test_a_batch_killed_mid_run_comes_back_finished(store, tmp_path):
    """The reported bug: the machine died at photo 37 of 38, and every later
    poll 404'd. It must come back done, with a reason naming where it stopped."""
    store.register("abc123", {"phase": "optimizing", "done": False}, uid="owner")
    store.update("abc123", current=37, total_photos=38)

    jobstore.reset()                     # the restart
    assert len(store.adopt_mirrors()) == 1

    snap = store.snapshot("abc123", "owner")
    assert snap["done"] is True
    assert "37 of 38" in snap["error"]


def test_a_batch_that_never_reached_drafting_does_not_promise_drafts():
    """A batch killed during the photo pass has written NOTHING to Drafts —
    it drafts only in the identify phase, which it never got to. Sending that
    seller to Drafts sends them to an empty list, which reads as lost work."""
    for phase in ("uploading", "optimizing", "grouping"):
        message = jobstore.interrupted_message(
            {"phase": phase, "total_photos": 38})
        assert "Drafts" not in message, phase
        assert "upload the photos again" in message, phase


def test_a_batch_that_did_reach_drafting_points_at_them():
    """The other half: once identifying starts, each finished item IS saved,
    so the seller needs to know they are there before re-running the rest."""
    message = jobstore.interrupted_message(
        {"phase": "identifying", "current": 4, "total_items": 12})
    assert "Drafts" in message and "item 4 of 12" in message


def test_an_adopted_job_keeps_its_owner(store):
    """Privacy has to survive the restart too."""
    store.register("abc123", {"phase": "identifying", "done": False}, uid="owner")
    jobstore.reset()
    store.adopt_mirrors()
    assert store.snapshot("abc123", "someone-else") is None
    assert store.snapshot("abc123", "owner") is not None


def test_a_batch_that_finished_is_adopted_as_it_was(store):
    """A job that completed just before the restart is not an interrupted one."""
    store.register("abc123", {"phase": "identifying", "done": False})
    store.update("abc123", phase="done", done=True)
    jobstore.reset()
    assert store.adopt_mirrors() == []
    snap = store.snapshot("abc123")
    assert snap["done"] is True and snap.get("error") is None


def test_a_batch_that_failed_keeps_its_own_reason(store):
    """A real error outranks the generic restart explanation."""
    store.register("abc123", {"phase": "optimizing", "done": False})
    store.update("abc123", done=True, error="No usable photos in the upload.")
    jobstore.reset()
    store.adopt_mirrors()
    assert store.snapshot("abc123")["error"] == "No usable photos in the upload."


@pytest.mark.parametrize("phase, fields, expected", [
    ("optimizing", {"current": 37, "total_photos": 38}, "preparing photo 37 of 38"),
    ("identifying", {"current": 4, "total_items": 12}, "identifying item 4 of 12"),
    ("grouping", {}, "sorting the photos into items"),
    ("uploading", {}, "receiving the photos"),
])
def test_the_reason_names_where_it_stopped(phase, fields, expected):
    assert expected in jobstore.interrupted_message({"phase": phase, **fields})


def test_a_single_item_identify_gets_its_own_reason():
    """The same endpoint serves per-item identifies. Telling that seller their
    finished items are in Drafts would be pointing at nothing."""
    message = jobstore.interrupted_message({"kind": "identify", "phase": "identifying"})
    assert "Drafts" not in message and "try again" in message


def test_the_reason_survives_a_job_with_no_progress_yet():
    """A job killed before it recorded a phase still gets a usable sentence.
    With no phase we cannot tell whether it drafted anything, so this is the
    one case that should point at Drafts without claiming what is in there."""
    message = jobstore.interrupted_message({})
    assert message.startswith("The server restarted,") and "Drafts" in message


def test_a_count_past_its_total_is_not_reported(store):
    """Progress ticks race the total; '39 of 38' would read as a bug."""
    assert "38 of 38" in jobstore.interrupted_message(
        {"phase": "optimizing", "current": 39, "total_photos": 38})


def test_adopting_never_clobbers_a_live_job(store):
    """Memory is always fresher than a job's own mirror. Adopting over a
    running batch would tell its poller the batch had died — the exact failure
    the mirror exists to prevent, caused by the cure."""
    store.register("abc123", {"phase": "optimizing", "done": False})
    store.update("abc123", current=37, total_photos=38)

    assert store.adopt_mirrors() == []

    snap = store.snapshot("abc123")
    assert snap["done"] is False and snap["current"] == 37


def test_the_verdict_is_not_written_back_to_the_mirror(store, tmp_path):
    """The mirror stays a record of last-known state, so the verdict can be
    re-derived on the next restart and no process rewrites another's record."""
    store.register("abc123", {"phase": "optimizing", "done": False})
    jobstore.reset()
    store.adopt_mirrors()

    on_disk = json.loads((tmp_path / "jobs" / "abc123.json").read_text())
    assert on_disk["done"] is False

    jobstore.reset()  # a second restart derives the same answer
    assert len(store.adopt_mirrors()) == 1
    assert store.snapshot("abc123")["done"] is True


def test_a_stray_temp_file_is_cleaned_up(store, tmp_path):
    """os.replace never ran for these — nothing else will ever claim them."""
    store.register("abc123", {"phase": "optimizing", "done": True})
    (tmp_path / "jobs" / "abc123.7f3a.tmp").write_text('{"partial"')

    jobstore.reset()
    store.adopt_mirrors()

    assert not list((tmp_path / "jobs").glob("*.tmp"))
    assert store.snapshot("abc123") is not None


def test_a_torn_mirror_is_skipped_not_fatal(store, tmp_path):
    """One unreadable file must not cost every other job its record."""
    store.register("good", {"phase": "optimizing", "done": False})
    (tmp_path / "jobs" / "torn.json").write_text('{"id": "torn", "phase":')
    jobstore.reset()
    store.adopt_mirrors()
    assert store.snapshot("good") is not None
    assert store.snapshot("torn") is None


def test_adopting_with_no_mirror_dir_is_quiet(store, tmp_path):
    """First boot on a fresh volume."""
    assert store.adopt_mirrors() == []


def test_old_mirrors_are_pruned_at_startup(store, tmp_path):
    store.register("stale", {"phase": "optimizing", "done": True})
    store.register("recent", {"phase": "optimizing", "done": True})
    old = time.time() - jobstore.MIRROR_TTL - 60
    os.utime(tmp_path / "jobs" / "stale.json", (old, old))

    jobstore.reset()
    store.adopt_mirrors()

    assert _mirror_files(tmp_path) == ["recent.json"]
    assert store.snapshot("stale") is None


def test_the_mirror_directory_stays_bounded(store, tmp_path, monkeypatch):
    """A long-lived machine can't accumulate records forever."""
    monkeypatch.setattr(jobstore, "MIRROR_MAX", 5)
    for i in range(12):
        store.register(f"job{i:02d}", {"phase": "done", "done": True})
        # Distinct mtimes: pruning keeps the newest, and a same-second batch
        # would make "newest" arbitrary.
        stamp = time.time() + i
        os.utime(tmp_path / "jobs" / f"job{i:02d}.json", (stamp, stamp))

    jobstore.reset()
    store.adopt_mirrors()

    assert _mirror_files(tmp_path) == [f"job{i:02d}.json" for i in range(7, 12)]


# --- snapshot_json: the cached, ready-to-serve body -------------------------
# Clients poll every 1.5s and a big batch is a ~1MB dict, so the JSON is built
# once per change rather than once per poll. The cache must never outlive the
# data it describes, or a poller sits on a stale phase forever.

def test_snapshot_json_matches_snapshot():
    jobstore.reset()
    jobstore.register("j1", {"phase": "optimizing", "done": False})
    assert json.loads(jobstore.snapshot_json("j1")) == jobstore.snapshot("j1")


def test_snapshot_json_refreshes_when_the_job_changes():
    jobstore.reset()
    jobstore.register("j1", {"phase": "optimizing", "done": False})
    first = jobstore.snapshot_json("j1")
    assert jobstore.snapshot_json("j1") is first  # unchanged: same cached body
    jobstore.update("j1", phase="identifying")
    assert json.loads(jobstore.snapshot_json("j1"))["phase"] == "identifying"


def test_snapshot_json_hides_internal_keys():
    jobstore.reset()
    jobstore.register("j1", {"phase": "grouping"}, uid="u1")
    body = json.loads(jobstore.snapshot_json("j1", "u1"))
    assert "_uid" not in body and "_rev" not in body


def test_snapshot_json_keeps_the_ownership_rule():
    jobstore.reset()
    jobstore.register("j1", {"phase": "grouping"}, uid="owner")
    assert jobstore.snapshot_json("j1", "owner") is not None
    assert jobstore.snapshot_json("j1", "someone-else") is None
    assert jobstore.snapshot_json("nope", "owner") is None


def test_a_reused_job_id_does_not_serve_the_old_body():
    jobstore.reset()
    jobstore.register("j1", {"phase": "optimizing"})
    jobstore.snapshot_json("j1")
    jobstore.register("j1", {"phase": "uploading"})  # fresh job, same id
    assert json.loads(jobstore.snapshot_json("j1"))["phase"] == "uploading"


# The app shell watches a running batch from every screen so its "processing"
# banner can stop the moment the batch ends. That poll only needs "is it done",
# and the full body is ~1MB of drafted items.

def test_brief_leaves_the_items_out():
    jobstore.reset()
    jobstore.register("j1", {"phase": "identifying", "done": False, "current": 2,
                             "items": [{"session_id": "s1"}, {"session_id": "s2"}]})
    body = json.loads(jobstore.brief_json("j1"))
    assert "items" not in body
    assert body["phase"] == "identifying" and body["current"] == 2
    assert len(jobstore.brief_json("j1")) < len(jobstore.snapshot_json("j1"))


def test_brief_still_answers_the_question_it_exists_for():
    jobstore.reset()
    jobstore.register("j1", {"phase": "identifying", "done": False})
    assert json.loads(jobstore.brief_json("j1"))["done"] is False
    jobstore.update("j1", done=True, phase="done")
    assert json.loads(jobstore.brief_json("j1"))["done"] is True


def test_brief_keeps_the_ownership_rule():
    jobstore.reset()
    jobstore.register("j1", {"phase": "grouping"}, uid="owner")
    assert jobstore.brief_json("j1", "owner") is not None
    assert jobstore.brief_json("j1", "someone-else") is None
    assert jobstore.brief_json("nope", "owner") is None


def test_a_reused_job_id_does_not_serve_the_old_brief():
    jobstore.reset()
    jobstore.register("j1", {"phase": "optimizing", "done": True})
    jobstore.brief_json("j1")
    jobstore.register("j1", {"phase": "uploading", "done": False})
    assert json.loads(jobstore.brief_json("j1"))["done"] is False

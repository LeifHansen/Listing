"""A photo name is a file name, never a path.

`Listing.images` is not server-owned: /api/save stores whatever the client
sends. Every reader then joins those names onto the session's photo folder,
so a name like "../../../secret.txt" reached:

  * the merge, which copied the file it pointed at into the target listing
    under a server-minted img_NNN.jpg -- and /media serves that to anyone, so
    any file the process could read (/proc/self/environ: every API key) was
    one save and one merge from public;
  * the AI fills, which base64'd the file and posted it to the model provider.

Two locks: the model drops such names wherever a Listing is built, and the
join sites resolve stored names through _photo_path, because a record saved
before the model guard existed still carries them in its raw dict.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend.models import Listing  # noqa: E402
from backend.tests.test_a_save_that_did_not_land_is_not_saved import (  # noqa: E402
    FakeDb)


@pytest.fixture
def api():
    from backend import main
    return main, TestClient(main.app)


SECRET = b"TOP-SECRET-ENV"


def test_the_model_keeps_only_bare_file_names():
    listing = Listing(images=["img_000.jpg", "../../etc/passwd", "a/b.jpg",
                              "..\\x.jpg", ".", "..", "", "img_001.jpg"])
    assert listing.images == ["img_000.jpg", "img_001.jpg"]


def test_a_save_cannot_store_a_path(api, monkeypatch):
    main, client = api
    fake = FakeDb(landed=True)
    monkeypatch.setattr(main, "db", fake)
    monkeypatch.setattr(main.deps, "db", fake)
    monkeypatch.setattr(main.deps, "assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main.storage, "save_listing", lambda *a, **k: None)

    res = client.post("/api/save/s1", json={
        "title": "src", "images": ["img_000.jpg", "../../../secret.txt"]})

    assert res.status_code == 200
    assert fake.rows["s1"]["listing"]["images"] == ["img_000.jpg"]


def test_a_merge_does_not_copy_a_file_from_outside_the_source(api, monkeypatch):
    """The record is planted RAW, as one saved before the model guard would
    be: the merge reads the source's images from the stored dict, which never
    passes through Listing."""
    main, client = api
    secret = main.config.SESSIONS_DIR.parent / "secret.txt"
    secret.write_bytes(SECRET)
    fake = FakeDb(rows={
        "t1": {"id": "t1", "listing": {"title": "target"}, "status": "draft"},
        "s1": {"id": "s1", "status": "draft",
               "listing": {"title": "src", "images": ["../../../secret.txt"]}},
    }, landed=True, deleted=True)
    monkeypatch.setattr(main, "db", fake)
    monkeypatch.setattr(main.deps, "db", fake)
    monkeypatch.setattr(main.deps, "assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main, "_purge_session_images_best_effort",
                        lambda *a, **k: None)

    res = client.post("/api/listings/merge",
                      json={"target_id": "t1", "source_ids": ["s1"]})

    assert res.status_code == 200, res.text
    assert res.json()["listing"]["images"] == []
    copied = list(main.storage.optimized_path("t1").glob("*")) \
        if main.storage.optimized_path("t1").is_dir() else []
    assert not any(p.read_bytes() == SECRET for p in copied)


def test_an_ai_fill_reads_only_files_inside_the_session(api):
    main, _client = api
    secret = main.config.SESSIONS_DIR.parent / "secret.txt"
    secret.write_bytes(SECRET)
    opt = main.storage.optimized_dir("fill1")
    (opt / "img_000.jpg").write_bytes(b"jpeg")
    # model_construct skips the validator: a Listing built some other way
    # must still not lead the fill outside the folder.
    listing = Listing.model_construct(images=["../../../secret.txt", "img_000.jpg"])

    paths = main._photos_for_fill("fill1", listing, {})

    assert [p.name for p in paths] == ["img_000.jpg"]

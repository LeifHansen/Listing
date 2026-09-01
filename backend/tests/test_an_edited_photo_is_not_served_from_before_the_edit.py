"""A photo the seller edited must not come back as the one they replaced.

Rotate, crop, auto-clean and background removal all rewrite the SAME file at
the SAME URL. /media used to be served `public, max-age=3600`, which is only
safe while the URL changes whenever the bytes do — and it doesn't. The
editor's cache-buster is a per-mount counter that starts at 0 again every time
the editor opens (imageVersions in useListingForm.js), so:

    open the editor      -> GET /media/.../a.jpg?v=0   (cached for an hour)
    rotate the photo     -> GET /media/.../a.jpg?v=1   (rotated, on screen)
    reopen the listing   -> GET /media/.../a.jpg?v=0   (served from cache:
                                                        the photo BEFORE the
                                                        rotation)

The seller rotated a photo, reopened the listing, and watched it come back
sideways. Nothing was wrong with the file on disk — the rotation had saved
correctly every time — so it looked like a rotate that neither rotated nor
saved, and no amount of re-rotating could fix it.

/media now says `no-cache`, which is not "don't cache" but "cache it, and ask
before you use it". The route answers that ask with a 304 and no body, so a
photo that hasn't changed still costs no bandwidth — the point of the old
header, kept, without the staleness that came with it.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main, storage  # noqa: E402


@pytest.fixture()
def photo(tmp_path, monkeypatch):
    """One optimized photo on disk, rewritable in place like a real edit."""
    opt = tmp_path / "s1" / "optimized"
    opt.mkdir(parents=True)
    (opt / "a.jpg").write_bytes(b"the original bytes")
    monkeypatch.setattr(storage, "optimized_path",
                        lambda sid: tmp_path / sid / "optimized")
    return opt / "a.jpg"


@pytest.fixture()
def client(photo):
    return TestClient(main.app)


def test_media_is_revalidated_not_assumed_fresh(client):
    r = client.get("/media/s1/optimized/a.jpg?v=0")
    assert r.status_code == 200
    # The exact header that let a rotated photo come back un-rotated.
    assert "max-age" not in r.headers["cache-control"]
    assert r.headers["cache-control"] == "no-cache"


def test_an_unchanged_photo_costs_no_bandwidth(client):
    """The reason the old header existed. Revalidation has to stay cheap or
    every reopen re-downloads all 24 photos."""
    first = client.get("/media/s1/optimized/a.jpg?v=0")
    etag = first.headers["etag"]
    assert etag

    again = client.get("/media/s1/optimized/a.jpg?v=0",
                       headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""


def test_a_weak_validator_still_counts_as_current(client):
    """Proxies and some browsers weaken an ETag in transit. A weakened tag for
    the same file is still the same file — failing to see that would re-send
    every photo on every view, which is the cost this is built to avoid."""
    etag = client.get("/media/s1/optimized/a.jpg").headers["etag"]
    r = client.get("/media/s1/optimized/a.jpg",
                   headers={"If-None-Match": f"W/{etag}"})
    assert r.status_code == 304


def test_the_rotated_photo_is_what_comes_back(client, photo):
    """The whole point, end to end: an edit at the SAME url, with the SAME
    stale cache-buster the editor sends after a reopen, and the client holding
    the pre-edit validator. It must be told the photo changed."""
    stale = client.get("/media/s1/optimized/a.jpg?v=0")
    before = stale.headers["etag"]

    photo.write_bytes(b"the rotated bytes, a different length")

    r = client.get("/media/s1/optimized/a.jpg?v=0",
                   headers={"If-None-Match": before})
    assert r.status_code == 200
    assert r.content == b"the rotated bytes, a different length"
    assert r.headers["etag"] != before


def test_a_client_holding_no_validator_gets_the_photo(client):
    """No If-None-Match, no 304 — a first view must never be answered empty."""
    r = client.get("/media/s1/optimized/a.jpg")
    assert r.status_code == 200
    assert r.content == b"the original bytes"


def test_a_stale_if_modified_since_gets_the_new_photo(client, photo):
    """The date-based validator, for clients that send it instead."""
    import os
    from email.utils import formatdate

    old = client.get("/media/s1/optimized/a.jpg")
    assert old.status_code == 200
    stamp = old.headers["last-modified"]

    # A real edit, moved clearly past the second the client was told about:
    # HTTP dates carry no sub-second precision, so an edit inside the same
    # second is not something this validator can report either way.
    photo.write_bytes(b"rotated")
    st = photo.stat()
    os.utime(photo, (st.st_atime, st.st_mtime + 5))

    r = client.get("/media/s1/optimized/a.jpg",
                   headers={"If-Modified-Since": stamp})
    assert r.status_code == 200
    assert r.content == b"rotated"

    # ...and the same request against the current file is a 304.
    current = formatdate(photo.stat().st_mtime, usegmt=True)
    assert client.get("/media/s1/optimized/a.jpg",
                      headers={"If-Modified-Since": current}).status_code == 304


def test_a_junk_validator_is_not_taken_as_proof_of_freshness(client):
    """An unparseable header must fall back to sending the photo, never to
    answering 304 on a guess."""
    for junk in ("not a date", "", "Sat, 99 Xxx 9999 99:99:99 GMT"):
        r = client.get("/media/s1/optimized/a.jpg",
                       headers={"If-Modified-Since": junk})
        assert r.status_code == 200, junk

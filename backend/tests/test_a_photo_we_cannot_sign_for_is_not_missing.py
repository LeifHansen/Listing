""""Not found" is a claim about the photo, and eBay acts on it.

`/media/{session}/optimized/{name}` serves the local file when it is there. It
is not always there: the reclaim pass frees local copies once they are safely
in R2, so an older listing's photos live only in the bucket and this route
redirects to them.

In presigned mode that redirect needs `objstore.url_for`, which answers None
when the presign fails — and the route fell through to `404 Not found`. The
object exists; we could not write a URL for it. The difference matters because
of who is asking: this URL is what a publish hands eBay as `<PictureURL>`, and
eBay's ingestion reads 404 as "there is no such photo" and drops or rejects it,
while a 5xx is something it will come back for.

Everything else about the route is unchanged: no local file and no object
store really is a 404, and public-URL mode never signs anything.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    from backend import main
    # A session directory that exists but holds no optimized copy of the photo.
    monkeypatch.setattr(main.storage, "optimized_path", lambda sid: tmp_path)
    return main, TestClient(main.app, raise_server_exceptions=False)


def _r2(main, monkeypatch, *, enabled=True, public=False, url=None):
    monkeypatch.setattr(main.objstore, "enabled", lambda: enabled)
    monkeypatch.setattr(main.config, "r2_public_urls", lambda: public)
    monkeypatch.setattr(main.objstore, "key_for", lambda sid, name: f"{sid}/{name}")
    monkeypatch.setattr(main.objstore, "url_for", lambda key, expires=0: url)
    monkeypatch.setattr(main.objstore, "public_url",
                        lambda key: f"https://cdn.test/{key}")


def test_a_presign_that_failed_is_not_a_missing_photo(client, monkeypatch):
    main, api = client
    _r2(main, monkeypatch, url=None)
    res = api.get("/media/s1/optimized/img_000.jpg", follow_redirects=False)
    assert res.status_code == 503, f"answered {res.status_code}: {res.text[:160]}"


def test_no_object_store_and_no_local_file_is_still_missing(client, monkeypatch):
    """The real 404: nowhere left to look."""
    main, api = client
    _r2(main, monkeypatch, enabled=False)
    res = api.get("/media/s1/optimized/img_000.jpg", follow_redirects=False)
    assert res.status_code == 404


def test_a_signed_url_still_redirects(client, monkeypatch):
    main, api = client
    _r2(main, monkeypatch, url="https://r2.test/s1/img_000.jpg?sig=abc")
    res = api.get("/media/s1/optimized/img_000.jpg", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"].startswith("https://r2.test/")


def test_public_url_mode_never_signs_and_still_redirects(client, monkeypatch):
    main, api = client
    _r2(main, monkeypatch, public=True, url=None)
    res = api.get("/media/s1/optimized/img_000.jpg?v=abc123",
                  follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"].startswith("https://cdn.test/")


def test_a_local_file_still_wins(client, monkeypatch, tmp_path):
    main, api = client
    (tmp_path / "img_000.jpg").write_bytes(b"\xff\xd8\xff local")
    _r2(main, monkeypatch, url=None)
    res = api.get("/media/s1/optimized/img_000.jpg", follow_redirects=False)
    assert res.status_code == 200 and res.content == b"\xff\xd8\xff local"

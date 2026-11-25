"""The studio reads the photo's own bytes, and saves what it was given.

Three things the on-device background remover needs to be true before it can
work at all, each of which was quietly false:

  * A canvas that drew a cross-origin image is tainted. /media redirects to
    the bucket once a photo is offloaded, and the iOS/Android shell's <img>
    is cross-origin on every photo, so the studio could not read back the
    pixels of exactly the photos a seller would most want to fix.
    /api/image/file streams the bytes instead -- never a redirect -- and says
    which bytes they were (X-Source-Sha256), so a mask computed from them can
    be refused if the photo changed meanwhile. `which=source` is the photo AS
    SHOT behind a cutout, so a second cut never segments a composite on white.

  * A cut-out saved with any transparency left in it reached the listing on a
    BLACK field: /api/edit-image did a bare convert("RGB").

  * A bulk item lost the photo as shot the moment the pile was split into
    items, because only optimized/ was copied across.
"""
from __future__ import annotations

import hashlib
import os
from io import BytesIO
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from backend import storage  # noqa: E402

UID = "u-studio"


def _png_with_a_hole() -> bytes:
    """A red square whose left half is fully transparent."""
    img = Image.new("RGBA", (40, 20), (200, 30, 30, 255))
    for x in range(20):
        for y in range(20):
            img.putpixel((x, y), (0, 0, 0, 0))
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture()
def client(dbmod, monkeypatch):
    assert dbmod.create_user(UID, "studio@example.com", "x")
    from backend import main
    monkeypatch.setattr(main.deps, "uid", lambda request: UID)
    monkeypatch.setattr(main.auth, "current_user", lambda request: {"id": UID})
    c = TestClient(main.app)
    c.main = main
    return c


def _photo(sid: str, colour=(10, 120, 200), name="img_000.jpg") -> Path:
    path = storage.optimized_dir(sid) / name
    Image.new("RGB", (60, 40), colour).save(path, "JPEG", quality=95)
    return path


def _pixel(path: Path, xy=(5, 5)):
    with Image.open(path) as im:
        return im.convert("RGB").getpixel(xy)


# --- saving ---------------------------------------------------------------------

def test_a_transparent_edit_is_saved_on_white_not_black(client):
    path = _photo("studio-save")
    res = client.post("/api/edit-image",
                      data={"session_id": "studio-save", "name": "img_000.jpg"},
                      files={"file": ("e.png", _png_with_a_hole(), "image/png")})
    assert res.status_code == 200, res.text
    hole = _pixel(path, (5, 10))
    assert min(hole) > 240, f"the transparent half came out {hole}, not white"
    kept = _pixel(path, (30, 10))
    assert kept[0] > 150 and kept[1] < 80, "the opaque half did not survive"


# --- reading ----------------------------------------------------------------------

def test_the_bytes_come_back_with_the_name_of_the_bytes(client):
    path = _photo("studio-read")
    res = client.get("/api/image/file/studio-read/img_000.jpg")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert res.content == path.read_bytes()
    assert res.headers["x-source-sha256"] == hashlib.sha256(res.content).hexdigest()


def test_the_source_of_a_cut_photo_is_the_photo_as_shot(client):
    """A cutout keeps the photo it replaced in as_shot/; a mask must be made
    from that, never from the composite on white."""
    path = _photo("studio-src", colour=(255, 255, 255))
    kept = path.parent.parent / "as_shot" / path.name
    kept.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (60, 40), (20, 160, 60)).save(kept, "JPEG", quality=95)
    os.utime(kept, (path.stat().st_mtime + 1, path.stat().st_mtime + 1))

    working = client.get("/api/image/file/studio-src/img_000.jpg")
    source = client.get("/api/image/file/studio-src/img_000.jpg?which=source")
    assert working.content == path.read_bytes()
    assert source.content == kept.read_bytes()
    assert source.headers["x-source-sha256"] != working.headers["x-source-sha256"]


def test_an_edit_after_the_cut_makes_the_edit_the_source(client):
    """The seller's own edit wins: once the working copy is newer than the
    kept copy, the source IS the working copy (images.as_shot's mtime rule)."""
    path = _photo("studio-edited", colour=(255, 255, 255))
    kept = path.parent.parent / "as_shot" / path.name
    kept.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (60, 40), (20, 160, 60)).save(kept, "JPEG")
    os.utime(kept, (path.stat().st_mtime - 10, path.stat().st_mtime - 10))
    source = client.get("/api/image/file/studio-edited/img_000.jpg?which=source")
    assert source.content == path.read_bytes()


def test_someone_elses_photo_is_not_found(client, dbmod):
    _photo("sess-theirs-file")
    assert dbmod.upsert_listing("sess-theirs-file",
                                {"title": "Not yours", "images": ["img_000.jpg"]},
                                user_id="someone-else")
    res = client.get("/api/image/file/sess-theirs-file/img_000.jpg")
    assert res.status_code == 404


def test_a_name_cannot_walk_out_of_the_session(client):
    _photo("studio-walk")
    for name in ("..%2F..%2Fsecret", "%2E%2E"):
        res = client.get(f"/api/image/file/studio-walk/{name}")
        assert res.status_code == 404, name


def test_a_photo_that_is_gone_says_so(client):
    storage.optimized_dir("studio-gone")
    res = client.get("/api/image/file/studio-gone/img_009.jpg")
    assert res.status_code == 404
    assert "re-upload" in res.json()["detail"]


def test_the_native_app_can_read_which_bytes_it_got(client):
    """The shell is cross-origin: a header it is not allowed to read is a
    header it does not have."""
    _photo("studio-cors")
    res = client.get("/api/image/file/studio-cors/img_000.jpg",
                     headers={"Origin": "capacitor://localhost"})
    exposed = res.headers.get("access-control-expose-headers", "").lower()
    assert "x-source-sha256" in exposed


# --- the bulk split ---------------------------------------------------------------

def test_a_bulk_item_keeps_the_photo_as_shot(client):
    main = client.main
    pile = storage.optimized_dir("pile-1")
    item = storage.optimized_dir("item-1")
    Image.new("RGB", (30, 30), (255, 255, 255)).save(pile / "img_004.jpg", "JPEG")
    kept_dir = pile.parent / "as_shot"
    kept_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (30, 30), (20, 160, 60)).save(kept_dir / "img_004.jpg", "JPEG")

    import shutil
    shutil.copyfile(pile / "img_004.jpg", item / "img_000.jpg")
    main._carry_photo_extras(pile, item, {"img_004.jpg": "img_000.jpg"})

    carried = item.parent / "as_shot" / "img_000.jpg"
    assert carried.read_bytes() == (kept_dir / "img_004.jpg").read_bytes()
    # And it is still the one images.as_shot trusts: at least as new as the
    # working copy written just before it.
    from backend.services import images
    assert images.as_shot(item / "img_000.jpg") == carried

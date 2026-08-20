"""Orientation: applied once, bounded, and durable once applied.

Three separate ways this was unreliable. EXIF applied more than once turns a
photo the wrong way; an unbounded AI pass holds an upload hostage; and a
rotation that isn't pushed to R2 before we call it done simply comes back
sideways later -- including on the copy eBay is handed at publish.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")
pytest.importorskip("numpy")

from pathlib import Path  # noqa: E402

from PIL import Image, ImageDraw, ImageOps  # noqa: E402

from backend.services import images, orient  # noqa: E402


def _sideways(tmp: Path) -> Path:
    """A landscape photo stored portrait with orientation=6, exactly as a
    phone held sideways records it."""
    img = Image.new("RGB", (900, 1200), (245, 245, 245))
    ImageDraw.Draw(img).rectangle((200, 400, 700, 800), fill=(40, 50, 70))
    data = Image.Exif()
    data[274] = 6
    src = tmp / "phone.jpg"
    img.save(src, "JPEG", quality=95, exif=data)
    return src


def test_exif_orientation_is_applied_exactly_once(tmp_path):
    """Applying it twice is a 90-degree error, and it is an easy mistake to
    make: any later stage that re-opens the file and 'helpfully' transposes
    again turns the photo the wrong way. The optimized output carries no EXIF
    at all, which is what makes a second application a no-op."""
    src = _sideways(tmp_path)
    images.optimize(src, tmp_path / "out")
    out = tmp_path / "out.jpg"
    with Image.open(out) as done:
        assert not done.getexif(), "the output still carries orientation EXIF"
        again = ImageOps.exif_transpose(done)
        assert again.size == done.size, "a second transpose would rotate it"


def test_the_orientation_pass_has_a_call_timeout_and_a_total_budget():
    """It runs on the upload path, in front of everything a seller waits for.
    The shared Claude client allows 120s x 3 attempts; orientation must not."""
    assert orient._CALL_TIMEOUT <= 30
    assert orient._TOTAL_BUDGET <= 120


def test_orientation_gives_up_rather_than_holding_the_upload(monkeypatch, tmp_path):
    """Past its budget, remaining batches are skipped and those photos stay as
    shot. For an enhancement that is the right answer; a batch that never
    finishes uploading is not."""
    paths = []
    for i in range(30):
        p = tmp_path / f"img_{i}.jpg"
        Image.new("RGB", (60, 60), (200, 200, 200)).save(p, "JPEG")
        paths.append(p)

    calls = []

    def _slow(batch):
        calls.append(len(batch))
        return {}

    monkeypatch.setattr(orient, "_enabled", lambda: True)
    monkeypatch.setattr(orient, "_TOTAL_BUDGET", -1.0)   # already over budget
    monkeypatch.setattr(orient, "_detect_batch", _slow)
    assert orient.detect_rotations(paths) == {}
    assert not calls, "batches ran after the budget was spent"


def test_a_photo_that_is_already_upright_is_left_alone(tmp_path):
    img = Image.new("RGB", (1200, 900), (245, 245, 245))
    ImageDraw.Draw(img).rectangle((300, 200, 900, 700), fill=(40, 50, 70))
    src = tmp_path / "upright.jpg"
    img.save(src, "JPEG", quality=95)
    out = images.optimize(src, tmp_path / "out")
    assert "rotated" not in out
    assert out["original_size"] == (1200, 900)


def test_a_manual_quarter_turn_is_applied_before_framing(tmp_path):
    """Rotation has to happen before the subject is measured, or the framing
    is computed from a sideways photo."""
    img = Image.new("RGB", (1200, 900), (245, 245, 245))
    ImageDraw.Draw(img).rectangle((300, 200, 900, 700), fill=(40, 50, 70))
    src = tmp_path / "turn.jpg"
    img.save(src, "JPEG", quality=95)
    out = images.optimize(src, tmp_path / "out", rotate=90)
    assert out["rotated"] == 90
    assert out["original_size"] == (900, 1200), "the turn was applied too late"


def test_a_nonsense_rotation_is_ignored_rather_than_resampling(tmp_path):
    """Anything but a clean quarter turn would resample -- blurring the photo
    and reporting a rotation that didn't happen."""
    src = _sideways(tmp_path)
    out = images.optimize(src, tmp_path / "out", rotate=37)
    assert "rotated" not in out

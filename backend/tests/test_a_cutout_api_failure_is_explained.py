"""A cutout that fails says why, in words the seller can act on.

The old lesson, from the engines that used to live here: a cutout that
silently did nothing -- or silently fell back to something worse -- is how
mangled photos kept getting saved without anyone knowing why. Photoroom is
now the only engine and there is nothing to fall back to, so every failure
carries its own sentence, and every sentence subclasses ValueError -- which is
what the studio route already maps to a 422 with the message in it
(backend/main.py, /api/image/remove-bg) and what optimize() turns into the
photo's `bg_error`.

Also pinned here: the shape of one cutout on Photoroom's wire (one multipart
POST to /v1/segment with the key in `x-api-key`, a PNG back), and what retries
and what does not. A 429 or a 5xx is a blip and retries with backoff. A 401,
402 or 403 is a fact about the app's account, is never retried, and is
logged for the operator while the seller reads only that the feature is
unavailable -- the key is the operator's to fix, not theirs.

This file needs httpx (httpx.post is monkeypatched on httpx itself, which is
what conftest's network ban asks for and what lets the engine import httpx
inside the call).
"""
from __future__ import annotations

import logging

import pytest

pytest.importorskip("PIL")
pytest.importorskip("httpx")

from io import BytesIO  # noqa: E402

from PIL import Image  # noqa: E402

from backend import config  # noqa: E402
from backend.services import cutout_api  # noqa: E402

KEY = "live-KEY-that-must-never-be-logged"


class _Resp:
    """The parts of an httpx response the engine reads."""

    def __init__(self, status=200, content=b"", headers=None, payload=None,
                 text=""):
        self.status_code = status
        self.content = content
        self.headers = headers or {}
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _png(size=(800, 600), box=(200, 150, 600, 450)) -> bytes:
    """A cutout as Photoroom returns it: RGBA, opaque inside `box`."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    if box:
        img.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]),
                            (40, 50, 70, 255)), (box[0], box[1]))
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _photo(size=(800, 600)) -> Image.Image:
    return Image.new("RGB", size, (238, 236, 232))


class _Photoroom:
    """A scripted Photoroom: records every POST and answers from a queue
    (the last answer repeats), or with a good cutout of what it was sent."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.answers: list = []

    def script(self, *answers):
        self.answers = list(answers)

    def post(self, url, **kw):
        self.calls.append((url, kw))
        if self.answers:
            answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
            if isinstance(answer, Exception):
                raise answer
            return answer
        sent = Image.open(BytesIO(kw["files"]["image_file"][1]))
        w, h = sent.size
        return _Resp(200, content=_png((w, h), (w // 4, h // 4, 3 * w // 4, 3 * h // 4)))


@pytest.fixture()
def photoroom(monkeypatch):
    """A key configured and the wire scripted."""
    import httpx
    monkeypatch.setattr(config, "PHOTOROOM_API_KEY", KEY)
    server = _Photoroom()
    monkeypatch.setattr(httpx, "post", server.post)
    return server


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retries are real; their waiting is not, or this file takes minutes."""
    monkeypatch.setattr(cutout_api.time, "sleep", lambda _s: None)


# --- no key means no opinion ------------------------------------------------

def test_an_unconfigured_engine_returns_none(monkeypatch):
    monkeypatch.setattr(config, "PHOTOROOM_API_KEY", "")
    assert cutout_api.photoroom_cutout(_photo()) is None


# --- one cutout, on the wire -----------------------------------------------

def test_a_cutout_comes_back_as_rgba(photoroom):
    cut = cutout_api.photoroom_cutout(_photo())
    assert cut.mode == "RGBA"
    assert cut.size == (800, 600)
    assert cut.split()[3].getbbox() == (200, 150, 600, 450)


def test_one_multipart_post_to_segment_with_the_key_in_the_header(photoroom):
    cutout_api.photoroom_cutout(_photo())
    assert len(photoroom.calls) == 1
    url, kw = photoroom.calls[0]
    assert url == "https://sdk.photoroom.com/v1/segment"
    assert kw["headers"] == {"x-api-key": KEY}
    name, payload, mime = kw["files"]["image_file"]
    assert mime == "image/jpeg"
    assert Image.open(BytesIO(payload)).format == "JPEG"
    # The matte alone: the white and the shadow are ours (images
    # ._compose_on_white), not the Image Editing plan's at five times the price.
    assert kw["data"] == {"format": "png"}


def test_the_upload_is_capped_rather_than_sending_a_4000px_photo(photoroom):
    cutout_api.photoroom_cutout(_photo((4032, 3024)))
    payload = photoroom.calls[0][1]["files"]["image_file"][1]
    assert max(Image.open(BytesIO(payload)).size) == cutout_api._MAX_SIDE


# --- the account's problems are the operator's ----------------------------

@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_is_unavailable_to_the_seller_and_named_to_the_operator(
        photoroom, caplog, status):
    photoroom.script(_Resp(status, payload={"detail": "Invalid API key"}))
    with caplog.at_level(logging.ERROR):
        with pytest.raises(cutout_api.PhotoroomError) as err:
            cutout_api.photoroom_cutout(_photo())
    assert str(err.value) == cutout_api.UNAVAILABLE
    assert "PHOTOROOM_API_KEY" in caplog.text
    assert len(photoroom.calls) == 1, "a refused key was retried"


def test_an_account_with_no_plan_or_no_images_left_is_not_retried(
        photoroom, caplog):
    photoroom.script(_Resp(402, payload={"detail": "No credits remaining"}))
    with caplog.at_level(logging.ERROR):
        with pytest.raises(cutout_api.PhotoroomError) as err:
            cutout_api.photoroom_cutout(_photo())
    assert str(err.value) == cutout_api.UNAVAILABLE
    assert "402" in caplog.text and "plan" in caplog.text
    assert len(photoroom.calls) == 1


# --- blips retry ------------------------------------------------------------

def test_rate_limiting_retries_and_then_gives_up_with_a_reason(photoroom):
    photoroom.script(_Resp(429, headers={"retry-after": "3"}))
    with pytest.raises(cutout_api.PhotoroomError) as err:
        cutout_api.photoroom_cutout(_photo())
    assert "try again in a minute" in str(err.value).lower()
    assert len(photoroom.calls) == cutout_api._TRIES


def test_a_blip_retries_and_then_succeeds(photoroom):
    photoroom.script(_Resp(503), _Resp(200, content=_png()))
    assert cutout_api.photoroom_cutout(_photo()) is not None
    assert len(photoroom.calls) == 2


def test_a_photo_photoroom_refuses_is_not_retried(photoroom):
    photoroom.script(_Resp(400, payload={"detail": "Unsupported image"}))
    with pytest.raises(cutout_api.PhotoroomError) as err:
        cutout_api.photoroom_cutout(_photo())
    assert "kept as shot" in str(err.value)
    assert len(photoroom.calls) == 1


def test_an_unexpected_status_is_named_and_keeps_the_photo(photoroom):
    photoroom.script(_Resp(418))
    with pytest.raises(cutout_api.PhotoroomError) as err:
        cutout_api.photoroom_cutout(_photo())
    assert "418" in str(err.value)
    assert "kept as shot" in str(err.value)


# --- nothing leaks ----------------------------------------------------------

def test_an_unreachable_service_does_not_leak_the_key(photoroom, caplog):
    class Boom(Exception):
        pass

    photoroom.script(Boom(f"connect failed for https://x?key={KEY}"))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(cutout_api.PhotoroomError) as err:
            cutout_api.photoroom_cutout(_photo())
    assert "Boom" in str(err.value)
    assert KEY not in str(err.value)
    assert KEY not in caplog.text
    assert len(photoroom.calls) == cutout_api._TRIES


def test_the_key_is_never_logged_on_a_refusal(photoroom, caplog):
    photoroom.script(_Resp(401, payload={"detail": "Invalid API key"}))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(cutout_api.PhotoroomError):
            cutout_api.photoroom_cutout(_photo())
    assert KEY not in caplog.text


def test_an_error_body_never_becomes_a_wall_of_someone_elses_json(photoroom):
    photoroom.script(_Resp(418, payload={"detail": "x" * 5000}))
    with pytest.raises(cutout_api.PhotoroomError) as err:
        cutout_api.photoroom_cutout(_photo())
    assert len(str(err.value)) < 200


# --- what comes back is checked --------------------------------------------

def test_an_empty_matte_is_reported_not_shipped(photoroom):
    photoroom.script(_Resp(200, content=_png(box=None)))
    with pytest.raises(cutout_api.PhotoroomError) as err:
        cutout_api.photoroom_cutout(_photo())
    assert "couldn't find an item" in str(err.value)


def test_something_that_is_not_an_image_is_reported(photoroom):
    photoroom.script(_Resp(200, content=b"<html>oops</html>"))
    with pytest.raises(cutout_api.PhotoroomError) as err:
        cutout_api.photoroom_cutout(_photo())
    assert "unreadable" in str(err.value)


def test_a_downscaled_answer_is_refused_rather_than_upscaled(photoroom):
    photoroom.script(_Resp(200, content=_png((200, 150), (50, 40, 150, 110))))
    with pytest.raises(cutout_api.PhotoroomError) as err:
        cutout_api.photoroom_cutout(_photo())
    assert "preview" in str(err.value)


def test_every_engine_error_is_a_valueerror():
    assert issubclass(cutout_api.PhotoroomError, cutout_api.CutoutApiError)
    assert issubclass(cutout_api.CutoutApiError, ValueError)


# --- what /api/ready says --------------------------------------------------

def test_the_engine_state_names_a_sandbox_key(monkeypatch):
    monkeypatch.setattr(config, "PHOTOROOM_API_KEY", "sandbox_abc")
    monkeypatch.setattr(config, "PHOTOROOM_SANDBOX", True)
    assert cutout_api.engine_state() == {
        "engine": "photoroom", "ready": True, "sandbox": True}


def test_a_sandbox_key_is_a_config_warning(fresh_config):
    cfg = fresh_config(PHOTOROOM_API_KEY="sandbox_abc")
    assert cfg.PHOTOROOM_SANDBOX is True
    assert any("sandbox key" in w for w in cfg.config_warnings())
    cfg = fresh_config(PHOTOROOM_API_KEY="live-key")
    assert cfg.PHOTOROOM_SANDBOX is False
    assert not any("sandbox key" in w for w in cfg.config_warnings())

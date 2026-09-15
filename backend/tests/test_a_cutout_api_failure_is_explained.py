"""A cutout API that fails says why, in words the seller can act on.

The old lesson, from the engines that used to live here: a paid cutout that
silently fell back to the weak local model is how mangled photos kept getting
saved without anyone knowing why. "Out of credits" and "that key is wrong" are
different problems with different fixes, and both are invisible if the only
symptom is that cutouts got worse.

So every failure carries its own sentence, and every sentence subclasses
ValueError -- which is what the studio route already maps to a 422 with the
message in it (backend/main.py, /api/image/remove-bg).

Also pinned here: what retries and what does not. A 429 or a 5xx is a blip and
retries with backoff. A 402 or a 403 is a fact about the account, and asking
three more times just spends three more seconds arriving at the same answer.

This file needs httpx, so it stays OUT of the Pillow-only `cutout` CI job (it
would SKIP there, and that job fails on a skip -- deliberately). httpx.post is
monkeypatched on the module under test, which is what conftest's network ban
asks for.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")
pytest.importorskip("httpx")

from io import BytesIO  # noqa: E402

from PIL import Image  # noqa: E402

from backend import config  # noqa: E402
from backend.services import cutout_api  # noqa: E402


class _Resp:
    """The parts of an httpx response these engines read."""

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
    """A cutout as a service would return it: RGBA, opaque inside `box`."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]),
                        (40, 50, 70, 255)), (box[0], box[1]))
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _photo(size=(800, 600)) -> Image.Image:
    return Image.new("RGB", size, (238, 236, 232))


@pytest.fixture()
def keyed(monkeypatch):
    """Both engines configured, so nothing returns None for lack of a key."""
    monkeypatch.setattr(config, "REMOVEBG_API_KEY", "test-key")
    monkeypatch.setattr(config, "LEONARDO_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retries are real; their waiting is not, or this file takes 30s."""
    monkeypatch.setattr(cutout_api.time, "sleep", lambda _s: None)


def _post(monkeypatch, *responses):
    """Stand in for httpx.post, answering with `responses` in order (the last
    one repeats). Returns the list of calls it received.

    Patched on httpx itself rather than on cutout_api, because the engines
    import httpx inside the call -- which is what keeps services/images
    importable in the CI job that has no httpx at all.
    """
    import httpx

    calls = []
    queue = list(responses)

    def _send(*args, **kwargs):
        calls.append(kwargs)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(httpx, "post", _send)
    return calls


# --- no key means no opinion ------------------------------------------------

def test_an_unconfigured_engine_returns_none(monkeypatch):
    """None is "not my turn", not a failure: it is what lets the chain fall
    through to the next engine without anybody being told anything."""
    monkeypatch.setattr(config, "REMOVEBG_API_KEY", "")
    monkeypatch.setattr(config, "LEONARDO_API_KEY", "")
    assert cutout_api.removebg_cutout(_photo()) is None
    assert cutout_api.leonardo_cutout(_photo()) is None


# --- the happy path ---------------------------------------------------------

def test_a_cutout_comes_back_as_rgba(keyed, monkeypatch):
    calls = _post(monkeypatch, _Resp(200, _png()))

    cut = cutout_api.removebg_cutout(_photo())

    assert cut is not None
    assert cut.mode == "RGBA"
    assert cut.split()[3].getbbox() is not None
    sent = calls[0]
    assert sent["headers"]["X-Api-Key"] == "test-key"
    assert sent["data"]["size"] == "auto", "full resolution, not a preview"
    assert sent["data"]["format"] == "png", "png or the alpha is lost"


def test_the_upload_is_capped_rather_than_sending_a_4000px_photo(keyed,
                                                                monkeypatch):
    """A 250-photo batch at full size is minutes of upload for an output that
    is only ever 1600px."""
    _post(monkeypatch, _Resp(200, _png()))
    payload = cutout_api.jpeg_payload(_photo((4032, 3024)))
    with Image.open(BytesIO(payload)) as img:
        assert max(img.size) <= 2048


# --- each failure says what it is ------------------------------------------

@pytest.mark.parametrize("status,expected", [
    (403, "rejected the API key"),
    (401, "rejected the API key"),
    (402, "out of credits"),
])
def test_a_hard_failure_names_its_cause(keyed, monkeypatch, status, expected):
    _post(monkeypatch, _Resp(status, b"", payload={"errors": [{"title": "no"}]}))

    with pytest.raises(cutout_api.RemoveBgError, match=expected):
        cutout_api.removebg_cutout(_photo())


def test_a_hard_failure_is_not_retried(keyed, monkeypatch):
    """Retrying a 402 spends three more seconds reaching the same answer."""
    calls = _post(monkeypatch, _Resp(402, b""))

    with pytest.raises(cutout_api.RemoveBgError):
        cutout_api.removebg_cutout(_photo())

    assert len(calls) == 1, "a spent account is not a blip"


def test_rate_limiting_retries_and_then_gives_up_with_a_reason(keyed,
                                                               monkeypatch):
    calls = _post(monkeypatch, _Resp(429, b"", headers={"retry-after": "1"}))

    with pytest.raises(cutout_api.RemoveBgError, match="rate-limiting"):
        cutout_api.removebg_cutout(_photo())

    assert len(calls) > 1, "429 is exactly the case worth retrying"


def test_a_blip_retries_and_then_succeeds(keyed, monkeypatch):
    calls = _post(monkeypatch, _Resp(503, b""), _Resp(200, _png()))

    cut = cutout_api.removebg_cutout(_photo())

    assert cut is not None
    assert len(calls) == 2, "the second attempt is the one that worked"


def test_an_unreachable_service_does_not_leak_the_url(keyed, monkeypatch):
    """httpx puts the request URL in its messages and the URL can carry the
    key, so only the exception TYPE is ever repeated."""
    import httpx

    def _boom(*args, **kwargs):
        raise httpx.ConnectError("connecting to https://api.remove.bg/?k=SECRET")

    monkeypatch.setattr(httpx, "post", _boom)

    with pytest.raises(cutout_api.RemoveBgError) as caught:
        cutout_api.removebg_cutout(_photo())

    assert "SECRET" not in str(caught.value)
    assert "ConnectError" in str(caught.value)


def test_an_empty_matte_is_reported_not_shipped(keyed, monkeypatch):
    blank = BytesIO()
    Image.new("RGBA", (800, 600), (0, 0, 0, 0)).save(blank, "PNG")
    _post(monkeypatch, _Resp(200, blank.getvalue()))

    with pytest.raises(cutout_api.RemoveBgError, match="couldn't find an item"):
        cutout_api.removebg_cutout(_photo())


def test_something_that_is_not_an_image_is_reported(keyed, monkeypatch):
    _post(monkeypatch, _Resp(200, b"<html>gateway timeout</html>"))

    with pytest.raises(cutout_api.RemoveBgError, match="unreadable image"):
        cutout_api.removebg_cutout(_photo())


def test_a_free_tier_preview_is_refused_rather_than_upscaled(keyed, monkeypatch):
    """remove.bg's free plan answers every request at 0.25MP whatever `size`
    asked for. Pasting a 625px cutout into a 1600px listing reads as this app
    being broken, so the plan limit is named instead."""
    _post(monkeypatch, _Resp(200, _png((625, 400), (100, 80, 500, 320))))

    with pytest.raises(cutout_api.RemoveBgError, match="free plan"):
        cutout_api.removebg_cutout(_photo((2400, 1800)))


# --- Leonardo, the successor engine ----------------------------------------

def test_leonardo_sends_a_bearer_token_and_names_the_model(keyed, monkeypatch):
    import base64
    calls = _post(monkeypatch, _Resp(
        200, payload={"generated_images": [
            {"image_base64": base64.b64encode(_png()).decode()}]}))

    cut = cutout_api.leonardo_cutout(_photo())

    assert cut is not None and cut.mode == "RGBA"
    sent = calls[0]
    assert sent["headers"]["Authorization"] == "Bearer test-key"
    assert sent["json"]["model"] == "remove-bg"


def test_leonardo_reads_a_url_as_well_as_base64(keyed, monkeypatch):
    """Which of the two v2 returns is the one open question in this
    integration, so both are handled and both are pinned."""
    import httpx
    _post(monkeypatch, _Resp(200, payload={
        "generated_images": [{"url": "https://cdn.leonardo.ai/out.png"}]}))
    monkeypatch.setattr(httpx, "get",
                        lambda *a, **k: _Resp(200, _png()))
    monkeypatch.setattr(_Resp, "raise_for_status", lambda self: None,
                        raising=False)

    assert cutout_api.leonardo_cutout(_photo()) is not None


def test_a_response_with_no_image_is_reported_as_a_shape_change(keyed,
                                                                monkeypatch):
    """If Leonardo changes its response shape, the seller gets a sentence and
    the photo survives — not a traceback."""
    _post(monkeypatch, _Resp(200, payload={"status": "COMPLETE"}))

    with pytest.raises(cutout_api.LeonardoError, match="carried no image"):
        cutout_api.leonardo_cutout(_photo())


def test_every_engine_error_is_a_valueerror(keyed):
    """What makes the studio route answer 422 with the reason in it, instead of
    500 with nothing."""
    for exc in (cutout_api.RemoveBgError, cutout_api.LeonardoError):
        assert issubclass(exc, cutout_api.CutoutApiError)
        assert issubclass(exc, ValueError)


def test_an_error_body_never_becomes_a_wall_of_someone_elses_json(keyed,
                                                                  monkeypatch):
    _post(monkeypatch, _Resp(418, b"", text="x" * 5000))

    with pytest.raises(cutout_api.RemoveBgError) as caught:
        cutout_api.removebg_cutout(_photo())

    assert len(str(caught.value)) < 400

"""A cutout API that fails says why, in words the seller can act on.

The old lesson, from the engines that used to live here: a paid cutout that
silently fell back to the weak local model is how mangled photos kept getting
saved without anyone knowing why. "Out of AI credits", "reconnect the Canva
account" and "that client secret is wrong" are different problems with
different fixes, and all three are invisible if the only symptom is that
cutouts got worse.

So every failure carries its own sentence, and every sentence subclasses
ValueError -- which is what the studio route already maps to a 422 with the
message in it (backend/main.py, /api/image/remove-bg).

Also pinned here: the shape of one cutout on Canva's wire (upload, transform,
design, export, download, delete), that the ROTATED refresh token is kept and
the seed falls back in only when the kept one is refused, and what retries and
what does not. A rate-limit 429 or a 5xx is a blip and retries with backoff. A
401, a 403 or a 429 about the account's AI CREDITS is a fact about the account,
and asking three more times just spends three more seconds arriving at the
same answer.

This file needs httpx, so it stays OUT of the Pillow-only `cutout` CI job (it
would SKIP there, and that job fails on a skip -- deliberately). httpx.post,
httpx.get and httpx.delete are monkeypatched on httpx itself, which is what
conftest's network ban asks for and what lets the engine import httpx inside
each call.
"""
from __future__ import annotations

import base64
import json

import pytest

pytest.importorskip("PIL")
pytest.importorskip("httpx")

from io import BytesIO  # noqa: E402

from PIL import Image  # noqa: E402

from backend import config  # noqa: E402
from backend.services import cutout_api  # noqa: E402

API = cutout_api.API
DOWNLOAD = "https://export-download.canva.com/x/0001.png?sig=SIGNED-SECRET"


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

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _png(size=(800, 600), box=(200, 150, 600, 450)) -> bytes:
    """A cutout as Canva exports it: RGBA, opaque inside `box`."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]),
                        (40, 50, 70, 255)), (box[0], box[1]))
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _photo(size=(800, 600)) -> Image.Image:
    return Image.new("RGB", size, (238, 236, 232))


def _job(status, **rest):
    return _Resp(200, payload={"job": {"status": status, **rest}})


def _token(access="at-1", refresh="rt-2"):
    return _Resp(200, payload={"access_token": access, "refresh_token": refresh,
                               "expires_in": 14400, "token_type": "Bearer"})


class _Canva:
    """A scripted Canva: answers every endpoint the engine talks to, records
    what it was asked, and lets a test break any one step with `script`."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.png = _png()
        self.cut_size = {"width": 800, "height": 600}
        # (method, url fragment) -> queue of responses; None means "answer
        # as normal". The last entry repeats.
        self.scripted: dict[tuple[str, str], list] = {}

    def script(self, method, fragment, *responses):
        self.scripted[(method, fragment)] = list(responses)

    def post(self, url, **kw):
        return self._route("POST", url, kw)

    def get(self, url, **kw):
        return self._route("GET", url, kw)

    def delete(self, url, **kw):
        return self._route("DELETE", url, kw)

    def sent(self, method, fragment):
        return [c for c in self.calls if c[0] == method and fragment in c[1]]

    def _route(self, method, url, kw):
        self.calls.append((method, url, kw))
        for (m, frag), queue in self.scripted.items():
            if m == method and frag in url and queue:
                answer = queue.pop(0) if len(queue) > 1 else queue[0]
                if answer is not None:
                    return answer
        return self._default(method, url)

    def _default(self, method, url):
        if url.endswith("/oauth/token"):
            return _token()
        if method == "POST" and url.endswith("/asset-uploads"):
            return _job("in_progress", id="up-1")
        if method == "GET" and "/asset-uploads/" in url:
            return _job("success", id="up-1", asset={"id": "src-1"})
        if method == "POST" and url.endswith("/image-transformations"):
            return _job("in_progress", id="tr-1")
        if method == "GET" and "/image-transformations/" in url:
            return _job("success", result={"asset": {
                "id": "cut-1", "metadata": {"type": "image", **self.cut_size}}})
        if method == "POST" and url.endswith("/designs"):
            return _Resp(200, payload={"design": {"id": "DAF1"}})
        if method == "POST" and url.endswith("/exports"):
            return _job("in_progress", id="ex-1")
        if method == "GET" and "/exports/" in url:
            return _job("success", id="ex-1", urls=[DOWNLOAD])
        if method == "GET" and url.startswith(DOWNLOAD):
            return _Resp(200, content=self.png)
        if method == "DELETE" and "/assets/" in url:
            return _Resp(204)
        raise AssertionError(f"the engine made an unexpected call: {method} {url}")


@pytest.fixture()
def canva(monkeypatch, tmp_path):
    """Canva connected, its token file in a scratch DATA_DIR, the wire
    scripted, and no waiting."""
    import httpx
    monkeypatch.setattr(config, "CANVA_CLIENT_ID", "client-id")
    monkeypatch.setattr(config, "CANVA_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(config, "CANVA_REFRESH_TOKEN", "rt-seed")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    cutout_api.forget_token()
    server = _Canva()
    monkeypatch.setattr(httpx, "post", server.post)
    monkeypatch.setattr(httpx, "get", server.get)
    monkeypatch.setattr(httpx, "delete", server.delete)
    yield server
    cutout_api.forget_token()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retries and polls are real; their waiting is not, or this file takes
    minutes."""
    monkeypatch.setattr(cutout_api.time, "sleep", lambda _s: None)


# --- no key means no opinion ------------------------------------------------

def test_an_unconfigured_engine_returns_none(monkeypatch):
    """None is "not my turn", not a failure: it is what lets the chain fall
    through to the next engine without anybody being told anything."""
    for name in ("CANVA_CLIENT_ID", "CANVA_CLIENT_SECRET", "CANVA_REFRESH_TOKEN"):
        monkeypatch.setattr(config, name, "")
    assert cutout_api.canva_cutout(_photo()) is None


def test_two_of_three_credentials_is_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "CANVA_CLIENT_ID", "client-id")
    monkeypatch.setattr(config, "CANVA_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(config, "CANVA_REFRESH_TOKEN", "")
    assert cutout_api.canva_cutout(_photo()) is None


# --- the happy path, and the shape of one cutout on the wire ----------------

def test_a_cutout_comes_back_as_rgba(canva):
    cut = cutout_api.canva_cutout(_photo())

    assert cut is not None
    assert cut.mode == "RGBA"
    assert cut.split()[3].getbbox() is not None


def test_the_photo_is_uploaded_as_raw_bytes_with_its_name_in_the_header(canva):
    cutout_api.canva_cutout(_photo())

    (_, _, kw), = canva.sent("POST", "/asset-uploads")
    assert kw["headers"]["Authorization"] == "Bearer at-1"
    assert kw["headers"]["Content-Type"] == "application/octet-stream"
    meta = json.loads(kw["headers"]["Asset-Upload-Metadata"])
    assert base64.b64decode(meta["name_base64"]).decode() == cutout_api.ASSET_NAME
    assert isinstance(kw["content"], bytes) and kw["content"][:2] == b"\xff\xd8", \
        "the body is the JPEG itself, not a form or JSON"


def test_the_remover_is_the_background_removal_transformation(canva):
    cutout_api.canva_cutout(_photo())

    (_, _, kw), = canva.sent("POST", "/image-transformations")
    assert kw["json"]["asset_id"] == "src-1", "the asset the upload produced"
    assert kw["json"]["transformations"] == [{"type": "background_removal"}]


def test_the_full_size_cutout_comes_out_through_a_transparent_png_export(canva):
    """An asset only exposes a thumbnail; the export is how the real pixels
    come back, and `transparent_background` is how the alpha survives."""
    canva.cut_size = {"width": 1234, "height": 777}

    cutout_api.canva_cutout(_photo())

    (_, _, design), = canva.sent("POST", "/designs")
    assert design["json"]["asset_id"] == "cut-1", "the transformed asset"
    assert design["json"]["design_type"] == {
        "type": "custom", "width": 1234, "height": 777}, \
        "a page exactly the size of the result, so nothing is scaled or inset"
    (_, _, export), = canva.sent("POST", "/exports")
    assert export["json"]["design_id"] == "DAF1"
    assert export["json"]["format"]["type"] == "png"
    assert export["json"]["format"]["transparent_background"] is True


def test_the_download_url_is_fetched_without_the_bearer_token(canva):
    """The signed URL is its own credential; sending ours to a CDN is a leak."""
    cutout_api.canva_cutout(_photo())

    (_, _, kw), = canva.sent("GET", "export-download")
    assert "Authorization" not in (kw.get("headers") or {})


def test_both_assets_are_deleted_afterwards(canva):
    cutout_api.canva_cutout(_photo())

    deleted = [url.rsplit("/", 1)[-1] for _, url, _ in canva.sent("DELETE", "/assets/")]
    assert sorted(deleted) == ["cut-1", "src-1"]


def test_a_failed_cleanup_never_costs_the_cutout(canva):
    canva.script("DELETE", "/assets/", _Resp(500, text="nope"))

    assert cutout_api.canva_cutout(_photo()) is not None


def test_the_upload_is_deleted_even_when_the_remover_fails(canva):
    canva.script("GET", "/image-transformations/",
                 _job("failed", error={"code": "internal_error", "message": "x"}))

    with pytest.raises(cutout_api.CanvaError):
        cutout_api.canva_cutout(_photo())

    assert [url.rsplit("/", 1)[-1] for _, url, _ in canva.sent("DELETE", "/assets/")] \
        == ["src-1"]


def test_the_upload_is_capped_rather_than_sending_a_4000px_photo(canva):
    """A 250-photo batch at full size is minutes of upload for an output that
    is only ever 1600px."""
    payload = cutout_api.jpeg_payload(_photo((4032, 3024)))
    with Image.open(BytesIO(payload)) as img:
        assert max(img.size) <= 2048


def test_a_job_that_finishes_at_once_is_not_polled(canva):
    """Canva may answer `success` straight from the create call, with no id;
    asking after a job that has no id would be a 404 and a lost photo."""
    canva.script("POST", "/asset-uploads",
                 _job("success", asset={"id": "src-1"}))

    assert cutout_api.canva_cutout(_photo()) is not None
    assert canva.sent("GET", "/asset-uploads/") == []


# --- the token: refreshed, rotated, kept ------------------------------------

def test_the_refresh_grant_carries_the_client_credentials_as_basic_auth(canva):
    cutout_api.canva_cutout(_photo())

    (_, url, kw), = canva.sent("POST", "/oauth/token")
    assert url == cutout_api.TOKEN_URL
    assert kw["auth"] == ("client-id", "client-secret")
    assert kw["data"] == {"grant_type": "refresh_token",
                          "refresh_token": "rt-seed"}


def test_the_rotated_refresh_token_is_kept_and_the_seed_is_not_spent_twice(
        canva):
    """Canva spends a refresh token when it is used. Reusing the seed on the
    next cutout would be refused -- and a restart must find the new one."""
    cutout_api.canva_cutout(_photo())
    cutout_api.canva_cutout(_photo())

    assert len(canva.sent("POST", "/oauth/token")) == 1, \
        "one token outlives one cutout"
    saved = json.loads(cutout_api.token_file().read_text())
    assert saved["refresh_token"] == "rt-2"

    cutout_api.forget_token()  # a restart
    cutout_api.canva_cutout(_photo())
    assert canva.sent("POST", "/oauth/token")[-1][2]["data"]["refresh_token"] \
        == "rt-2", "the kept token, not the seed"


def test_a_refused_kept_token_falls_back_to_the_seed(canva):
    """What re-seeding CANVA_REFRESH_TOKEN after a breakage relies on."""
    cutout_api.token_file().write_text(json.dumps({"refresh_token": "rt-stale"}))
    canva.script("POST", "/oauth/token",
                 _Resp(400, payload={"code": "invalid_grant",
                                     "message": "Invalid refresh token"}),
                 _token(refresh="rt-3"))

    assert cutout_api.canva_cutout(_photo()) is not None

    tried = [c[2]["data"]["refresh_token"] for c in canva.sent("POST", "/oauth/token")]
    assert tried == ["rt-stale", "rt-seed"]
    assert json.loads(cutout_api.token_file().read_text())["refresh_token"] == "rt-3"


def test_a_spent_refresh_token_says_to_reconnect(canva):
    canva.script("POST", "/oauth/token",
                 _Resp(400, payload={"code": "invalid_grant",
                                     "message": "Invalid refresh token"}))

    with pytest.raises(cutout_api.CanvaError, match="canva_authorize"):
        cutout_api.canva_cutout(_photo())


def test_bad_client_credentials_name_the_secrets(canva):
    canva.script("POST", "/oauth/token",
                 _Resp(401, payload={"code": "invalid_client",
                                     "message": "Client authentication failed"}))

    with pytest.raises(cutout_api.CanvaError, match="CANVA_CLIENT_SECRET"):
        cutout_api.canva_cutout(_photo())


def test_a_401_mid_cutout_refreshes_once_and_carries_on(canva):
    """A token revoked under us is a refresh, not a lost photo."""
    canva.script("POST", "/asset-uploads",
                 _Resp(401, payload={"code": "invalid_access_token"}), None)

    assert cutout_api.canva_cutout(_photo()) is not None
    assert len(canva.sent("POST", "/oauth/token")) == 2


def test_a_401_that_survives_a_refresh_says_to_reconnect(canva):
    canva.script("POST", "/asset-uploads",
                 _Resp(401, payload={"code": "invalid_access_token"}))

    with pytest.raises(cutout_api.CanvaError, match="Reconnect"):
        cutout_api.canva_cutout(_photo())


def test_a_token_is_not_refreshed_while_it_is_fresh(canva, monkeypatch):
    cutout_api.canva_cutout(_photo())
    cutout_api.canva_cutout(_photo())
    assert len(canva.sent("POST", "/oauth/token")) == 1

    # ...and is, once it is about to expire.
    late = cutout_api._TOKEN["expires_at"] - 10
    monkeypatch.setattr(cutout_api.time, "time", lambda: late)
    cutout_api.canva_cutout(_photo())
    assert len(canva.sent("POST", "/oauth/token")) == 2


# --- each failure says what it is ------------------------------------------

def test_the_preview_api_not_being_enabled_is_named(canva):
    canva.script("POST", "/image-transformations",
                 _Resp(403, payload={"code": "permission_denied",
                                     "message": "Image transformation is not enabled"}))

    with pytest.raises(cutout_api.CanvaError, match="PREVIEW API"):
        cutout_api.canva_cutout(_photo())


def test_running_out_of_ai_credits_is_named_and_not_retried(canva):
    """A 429 about the credit quota is a fact about the account for the rest
    of the month; the rate-limit retry loop must not treat it as a blip."""
    canva.script("POST", "/image-transformations",
                 _Resp(429, payload={"code": "credit_quota_exceeded",
                                     "message": "User has exceeded their credit quota"}))

    with pytest.raises(cutout_api.CanvaError, match="AI credits"):
        cutout_api.canva_cutout(_photo())

    assert len(canva.sent("POST", "/image-transformations")) == 1


def test_a_job_that_runs_out_of_credits_is_named(canva):
    canva.script("GET", "/image-transformations/",
                 _job("failed", error={"code": "quota_exceeded", "message": "x"}))

    with pytest.raises(cutout_api.CanvaError, match="AI credits"):
        cutout_api.canva_cutout(_photo())


def test_rate_limiting_retries_and_then_gives_up_with_a_reason(canva):
    canva.script("POST", "/exports",
                 _Resp(429, headers={"retry-after": "1"},
                       payload={"code": "too_many_requests", "message": "slow down"}))

    with pytest.raises(cutout_api.CanvaError, match="rate-limiting"):
        cutout_api.canva_cutout(_photo())

    assert len(canva.sent("POST", "/exports")) > 1, \
        "a rate limit is exactly the case worth retrying"


def test_a_blip_retries_and_then_succeeds(canva):
    canva.script("POST", "/designs", _Resp(503, text="unavailable"), None)

    assert cutout_api.canva_cutout(_photo()) is not None
    assert len(canva.sent("POST", "/designs")) == 2, \
        "the second attempt is the one that worked"


def test_a_hard_failure_is_not_retried(canva):
    canva.script("POST", "/image-transformations",
                 _Resp(403, payload={"code": "permission_denied", "message": "no"}))

    with pytest.raises(cutout_api.CanvaError):
        cutout_api.canva_cutout(_photo())

    assert len(canva.sent("POST", "/image-transformations")) == 1


def test_no_foreground_is_reported_as_nothing_to_cut(canva):
    canva.script("GET", "/image-transformations/",
                 _job("failed", error={
                     "code": "no_foreground_found",
                     "message": "No foreground subject was found in the image"}))

    with pytest.raises(cutout_api.CanvaError, match="couldn't find an item"):
        cutout_api.canva_cutout(_photo())


def test_a_plan_that_cannot_export_transparent_pngs_is_named(canva):
    canva.script("GET", "/exports/",
                 _job("failed", id="ex-1", error={"code": "license_required",
                                                 "message": "x"}))

    with pytest.raises(cutout_api.CanvaError, match="Canva Pro"):
        cutout_api.canva_cutout(_photo())


def test_an_unknown_job_failure_quotes_canva_and_keeps_the_photo(canva):
    canva.script("GET", "/asset-uploads/",
                 _job("failed", id="up-1", error={"code": "import_failed",
                                                 "message": "Could not read the file"}))

    with pytest.raises(cutout_api.CanvaError, match="Could not read the file"):
        cutout_api.canva_cutout(_photo())


def test_a_job_that_never_finishes_is_given_up_on(canva, monkeypatch):
    canva.script("GET", "/image-transformations/", _job("in_progress", id="tr-1"))
    monkeypatch.setattr(cutout_api, "_JOB_TIMEOUT", -1.0)

    with pytest.raises(cutout_api.CanvaError, match="took too long"):
        cutout_api.canva_cutout(_photo())


def test_an_unreachable_service_does_not_leak_the_url(canva, monkeypatch):
    """httpx puts the request URL in its messages and a URL can carry a
    credential, so only the exception TYPE is ever repeated."""
    import httpx

    def _boom(*args, **kwargs):
        raise httpx.ConnectError("connecting to https://api.canva.com/?k=SECRET")

    monkeypatch.setattr(httpx, "post", _boom)

    with pytest.raises(cutout_api.CanvaError) as caught:
        cutout_api.canva_cutout(_photo())

    assert "SECRET" not in str(caught.value)
    assert "ConnectError" in str(caught.value)


def test_a_failed_download_does_not_leak_the_signed_url(canva, monkeypatch):
    import httpx

    def _get(url, **kw):
        if url.startswith(DOWNLOAD):
            raise httpx.ConnectError(f"connecting to {url}")
        return canva.get(url, **kw)

    monkeypatch.setattr(httpx, "get", _get)

    with pytest.raises(cutout_api.CanvaError) as caught:
        cutout_api.canva_cutout(_photo())

    assert "SIGNED-SECRET" not in str(caught.value)


def test_an_empty_matte_is_reported_not_shipped(canva):
    blank = BytesIO()
    Image.new("RGBA", (800, 600), (0, 0, 0, 0)).save(blank, "PNG")
    canva.png = blank.getvalue()

    with pytest.raises(cutout_api.CanvaError, match="couldn't find an item"):
        cutout_api.canva_cutout(_photo())


def test_something_that_is_not_an_image_is_reported(canva):
    canva.png = b"<html>gateway timeout</html>"

    with pytest.raises(cutout_api.CanvaError, match="unreadable image"):
        cutout_api.canva_cutout(_photo())


def test_a_downscaled_export_is_refused_rather_than_upscaled(canva):
    """Pasting a 300px cutout into a 1600px listing reads as this app being
    broken, so what came back is named instead."""
    canva.png = _png((300, 225), (50, 40, 250, 190))

    with pytest.raises(cutout_api.CanvaError, match="preview"):
        cutout_api.canva_cutout(_photo((2400, 1800)))


def test_a_response_with_no_result_is_reported_as_a_shape_change(canva):
    """If Canva changes its response shape, the seller gets a sentence and
    the photo survives — not a traceback."""
    canva.script("GET", "/image-transformations/", _job("success"))

    with pytest.raises(cutout_api.CanvaError, match="shape may have changed"):
        cutout_api.canva_cutout(_photo())


def test_every_engine_error_is_a_valueerror():
    """What makes the studio route answer 422 with the reason in it, instead of
    500 with nothing."""
    assert issubclass(cutout_api.CanvaError, cutout_api.CutoutApiError)
    assert issubclass(cutout_api.CanvaError, ValueError)


def test_an_error_body_never_becomes_a_wall_of_someone_elses_json(canva):
    canva.script("POST", "/asset-uploads", _Resp(418, text="x" * 5000))

    with pytest.raises(cutout_api.CanvaError) as caught:
        cutout_api.canva_cutout(_photo())

    assert len(str(caught.value)) < 400

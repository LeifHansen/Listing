"""Background removal on the seller's own device: free, unasked, and patient.

The matte is made on the phone or in the browser; the server only composites
it. So none of Photoroom's gates apply to it -- nothing is sent anywhere,
nothing is charged -- and the photos the device has not reached yet simply
wait, as shot, for the next time the app is open. Photoroom stays as the
fallback, behind every gate it always had.

What is pinned here:

  * an upload asking for device cutouts is never refused for a switch that
    is off and never charged; its photos are written into the queue;
  * the queue lists only the seller's own drafts' photos;
  * a matte is applied only to the photo it was made from (409 otherwise),
    is refused when it kept nothing, keeps the shot and the matte beside the
    new photo, costs nothing, and is never written over a live listing by
    the background queue;
  * the seller's own edit, a restore and a delete all retire what waits;
  * the cloud fallback is paid exactly like every Photoroom cutout, is never
    paid twice for the same photo, gives back what failed, and stops at the
    app's monthly budget and at its kill switch;
  * the studio cuts a picture by its edge, and from the photo as shot;
  * what the apps may do on the device is the server's to say;
  * a cutout can be handed over transparent, and put on the seller's colour.
"""
from __future__ import annotations

import threading
import time
from io import BytesIO
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from backend import config, storage  # noqa: E402
from backend.services import cutout_api, cutouts, images  # noqa: E402

UID = "u-device"
W, H = 200, 150


def _jpeg(colour=(200, 180, 160)) -> bytes:
    img = Image.new("RGB", (W, H), (235, 235, 235))
    ImageDraw.Draw(img).ellipse((60, 35, 140, 115), fill=colour)
    buf = BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


def _mask_png(fill=255, size=(W, H)) -> bytes:
    m = Image.new("L", size, 0)
    if fill:
        ImageDraw.Draw(m).ellipse((60 * size[0] // W, 35 * size[1] // H,
                                   140 * size[0] // W, 115 * size[1] // H),
                                  fill=fill)
    buf = BytesIO()
    m.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture()
def client(dbmod, monkeypatch):
    assert dbmod.create_user(UID, "device@example.com", "x")
    from backend import main
    monkeypatch.setattr(main.deps, "uid", lambda request: UID)
    monkeypatch.setattr(main.deps, "signed_in_uid", lambda request: UID)
    monkeypatch.setattr(main.auth, "current_user", lambda request: {"id": UID})
    monkeypatch.setattr(config, "PHOTOROOM_API_KEY", "live-key")
    monkeypatch.setattr(config, "PHOTOROOM_SANDBOX", False)
    monkeypatch.setattr(config, "PHOTOROOM_ENABLED", True)
    monkeypatch.setattr(config, "PHOTOROOM_MONTHLY_BUDGET_USD", None)
    monkeypatch.setattr(config, "BG_REMOVAL_MONTHLY_LIMIT", 100)
    charged: list = []

    def _charge(request, feature, units=1):
        charged.append((feature, units))
        return None

    monkeypatch.setattr(main.deps, "charge_ai", _charge)

    def _no_photoroom(_img):
        raise AssertionError("Photoroom was called")

    monkeypatch.setattr(cutout_api, "photoroom_cutout", _no_photoroom)
    c = TestClient(main.app)
    c.charged = charged
    c.main = main
    c.db = dbmod
    return c


def _draft(c, sid: str, names=("img_000.jpg",), status="draft",
           user=UID) -> Path:
    """A saved draft with photos on disk, and their rows pending."""
    opt = storage.optimized_dir(sid)
    for n in names:
        (opt / n).write_bytes(_jpeg())
    assert c.db.upsert_listing(sid, {"title": "Thing", "images": list(names)},
                               status=status, user_id=user)
    c.db.cutout_mark([{"session_id": sid, "name": n} for n in names], user)
    return opt


def _source(c, sid, name="img_000.jpg"):
    res = c.get(f"/api/image/file/{sid}/{name}?which=source")
    assert res.status_code == 200
    return res.headers["x-source-sha256"]


def _apply(c, sid, sha, name="img_000.jpg", mask=None, **form):
    data = {"session_id": sid, "name": name, "source_sha": sha,
            "provider": "apple-vision", "platform": "ios", "origin": "queue",
            **form}
    return c.post("/api/cutout/apply", data=data,
                  files={"mask": ("m.png", mask or _mask_png(), "image/png")})


# --- uploading for the device --------------------------------------------------------

def test_a_device_upload_is_never_refused_for_the_switch_and_never_charged(client):
    """Background removal is OFF for this seller (the consent switch), and
    the device does not need it on: nothing leaves the device."""
    files = [("files", ("a.jpg", _jpeg(), "image/jpeg"))]
    res = client.post("/api/upload", files=files,
                      data={"remove_bg": "false", "cutout": "device"})
    assert res.status_code == 200, res.text
    assert client.charged == []
    body = res.json()
    assert body["optimize_results"][0]["bg_pending"] == "item"
    sid = body["session_id"]
    row = client.db.cutout_get(sid, "img_000.jpg")
    assert row["state"] == "pending" and row["kind"] == "item"
    assert row["user_id"] == UID


def test_remove_bg_true_with_device_is_still_a_paid_request(client):
    """remove_bg is the paid request, whatever else rides along; the device
    asks with remove_bg=false. So a client that sends both gets exactly what
    it got before this existed: the consent gate."""
    files = [("files", ("a.jpg", _jpeg(), "image/jpeg"))]
    res = client.post("/api/upload", files=files,
                      data={"remove_bg": "true", "cutout": "device"})
    assert res.status_code == 409


def test_add_photos_for_the_device_are_queued_too(client, monkeypatch):
    sid = "dev-more"
    _draft(client, sid)
    client.db.cutout_forget(sid)
    files = [("files", ("b.jpg", _jpeg(), "image/jpeg"))]
    res = client.post(f"/api/upload-more/{sid}", files=files,
                      data={"remove_bg": "false", "cutout": "device"})
    assert res.status_code == 200, res.text
    job = res.json()["job_id"]
    for _ in range(200):
        st = client.get(f"/api/bulk/status/{job}").json()
        if st.get("done"):
            break
        time.sleep(0.05)
    assert st["result"]["optimize_results"][0]["bg_pending"] == "item"
    assert client.db.cutout_get(sid, "img_001.jpg")["state"] == "pending"
    assert client.charged == []


# --- the queue ------------------------------------------------------------------------

def test_the_queue_lists_only_the_sellers_own_drafts(client):
    _draft(client, "dev-q-mine")
    _draft(client, "dev-q-live", status="published")
    _draft(client, "dev-q-theirs", user="someone-else")
    photos = client.get("/api/cutout/pending").json()["photos"]
    sessions = {p["session_id"] for p in photos}
    assert "dev-q-mine" in sessions
    assert "dev-q-live" not in sessions
    assert "dev-q-theirs" not in sessions


def test_a_photo_the_device_gave_up_on_stops_being_offered(client):
    _draft(client, "dev-skip")
    res = client.post("/api/cutout/skip", json={
        "session_id": "dev-skip", "name": "img_000.jpg",
        "reason": "Not supported on this device", "provider": "mlkit",
        "platform": "android"})
    assert res.status_code == 200
    row = client.db.cutout_get("dev-skip", "img_000.jpg")
    assert row["state"] == "failed" and "Not supported" in row["error"]
    assert not any(p["session_id"] == "dev-skip"
                   for p in client.get("/api/cutout/pending").json()["photos"])


# --- applying a matte ---------------------------------------------------------------------

def test_a_matte_becomes_the_listing_photo_for_free(client):
    opt = _draft(client, "dev-apply")
    sha = _source(client, "dev-apply")
    res = _apply(client, "dev-apply", sha)
    assert res.status_code == 200, res.text
    assert res.json()["engine"] == "device"
    path = opt / "img_000.jpg"
    with Image.open(path) as im:
        assert im.convert("RGB").getpixel((5, 5)) == (255, 255, 255)
    assert images.as_shot(path) != path, "the photo as shot was not kept"
    assert images.current_mask(path) is not None, "the matte was not kept"
    row = client.db.cutout_get("dev-apply", "img_000.jpg")
    assert row["state"] == "done" and row["provider"] == "apple-vision"
    assert row["platform"] == "ios" and row["working_sha"]
    assert client.charged == []
    usage = client.db.cutout_usage(time.strftime("%Y-%m", time.gmtime()))
    assert {"provider": "apple-vision", "platform": "ios", "photos": 1} in usage


def test_a_second_cut_is_made_from_the_shot_not_the_composite(client):
    _draft(client, "dev-twice")
    shot = (storage.optimized_dir("dev-twice") / "img_000.jpg").read_bytes()
    assert _apply(client, "dev-twice", _source(client, "dev-twice")).status_code == 200
    again = client.get("/api/image/file/dev-twice/img_000.jpg?which=source")
    assert again.content == shot


def test_a_matte_of_a_photo_that_changed_is_refused(client):
    opt = _draft(client, "dev-changed")
    before = (opt / "img_000.jpg").read_bytes()
    res = _apply(client, "dev-changed", "0" * 64)
    assert res.status_code == 409
    assert (opt / "img_000.jpg").read_bytes() == before


def test_a_matte_that_kept_nothing_is_refused_and_recorded(client):
    opt = _draft(client, "dev-empty")
    before = (opt / "img_000.jpg").read_bytes()
    res = _apply(client, "dev-empty", _source(client, "dev-empty"),
                 mask=_mask_png(fill=0))
    assert res.status_code == 422
    assert "kept as shot" in res.json()["detail"]
    assert (opt / "img_000.jpg").read_bytes() == before
    assert client.db.cutout_get("dev-empty", "img_000.jpg")["state"] == "failed"


def test_an_unreadable_matte_is_a_422_not_a_500(client):
    _draft(client, "dev-garbage")
    res = _apply(client, "dev-garbage", _source(client, "dev-garbage"),
                 mask=b"not a png at all")
    assert res.status_code == 422


def test_the_queue_never_cuts_a_live_listing(client):
    _draft(client, "dev-live", status="published")
    res = _apply(client, "dev-live", _source(client, "dev-live"))
    assert res.status_code == 409
    assert client.db.cutout_get("dev-live", "img_000.jpg")["state"] == "skipped"


def test_the_studio_may_cut_a_live_listing(client):
    """The seller, in the studio, on their own photo: their call."""
    _draft(client, "dev-live-studio", status="published")
    res = _apply(client, "dev-live-studio", _source(client, "dev-live-studio"),
                 origin="studio")
    assert res.status_code == 200, res.text


def test_someone_elses_photo_cannot_be_cut(client):
    _draft(client, "dev-theirs-apply", user="someone-else")
    res = client.post("/api/cutout/apply", data={
        "session_id": "dev-theirs-apply", "name": "img_000.jpg",
        "source_sha": ""}, files={"mask": ("m.png", _mask_png(), "image/png")})
    assert res.status_code == 404


def test_a_picture_is_cut_by_its_edge_even_from_a_device_matte(client, monkeypatch):
    """The matte of a painting is the subject lifted out of it; it may only
    say where the painting's edge is, and a subject is not a rectangle."""
    opt = _draft(client, "dev-art")
    images.note_flags(opt / "img_000.jpg", art=True)
    monkeypatch.setattr(images.artwork, "outline", lambda _rgb: None)
    res = _apply(client, "dev-art", _source(client, "dev-art"))
    assert res.status_code == 422
    assert "picture" in res.json()["detail"]


# --- the seller's edit wins ------------------------------------------------------------------

def test_an_edit_in_the_studio_retires_what_waits(client):
    _draft(client, "dev-edit")
    res = client.post("/api/edit-image",
                      data={"session_id": "dev-edit", "name": "img_000.jpg"},
                      files={"file": ("e.jpg", _jpeg((10, 10, 10)), "image/jpeg")})
    assert res.status_code == 200
    assert client.db.cutout_get("dev-edit", "img_000.jpg")["state"] == "skipped"


def test_a_restore_retires_what_waits(client):
    opt = _draft(client, "dev-restore")
    (storage.original_dir("dev-restore") / "src_000.jpg").write_bytes(_jpeg())
    assert opt.exists()
    res = client.post("/api/image/restore-original",
                      data={"session_id": "dev-restore", "name": "img_000.jpg"})
    assert res.status_code == 200, res.text
    assert client.db.cutout_get("dev-restore", "img_000.jpg")["state"] == "skipped"


def test_a_deleted_photo_is_forgotten(client):
    _draft(client, "dev-del")
    res = client.post("/api/delete-image",
                      json={"session_id": "dev-del", "name": "img_000.jpg"})
    assert res.status_code == 200
    assert client.db.cutout_get("dev-del", "img_000.jpg") is None


def test_a_deleted_listing_takes_its_queue_with_it(client):
    _draft(client, "dev-gone")
    assert client.db.delete_listing("dev-gone", UID)
    assert client.db.cutout_get("dev-gone", "img_000.jpg") is None


def test_a_rotate_turns_the_shot_and_the_matte_with_the_photo(client):
    opt = _draft(client, "dev-rot")
    assert _apply(client, "dev-rot", _source(client, "dev-rot")).status_code == 200
    path = opt / "img_000.jpg"
    res = client.post("/api/rotate-image",
                      json={"session_id": "dev-rot", "name": "img_000.jpg"})
    assert res.status_code == 200
    shot, mask = images.as_shot(path), images.current_mask(path)
    assert shot != path and mask is not None, "rotation made them stale"
    with Image.open(shot) as s, Image.open(mask) as m:
        assert s.size == (H, W) and m.size == (H, W)


# --- the split of a bulk pile -----------------------------------------------------------------

def test_the_queue_follows_a_photo_into_its_items_session(client):
    db = client.db
    db.cutout_mark([{"session_id": "pile", "name": "img_007.jpg",
                     "kind": "art"}], UID)
    assert db.cutout_copy("pile", "item", {"img_007.jpg": "img_000.jpg"}, UID)
    assert db.cutout_copy("pile", "item", {"img_007.jpg": "img_000.jpg"}, UID)
    row = db.cutout_get("item", "img_000.jpg")
    assert row["state"] == "pending" and row["kind"] == "art"


# --- the cloud fallback -----------------------------------------------------------------------

@pytest.fixture()
def photoroom(monkeypatch):
    calls = []

    def _cut(img):
        calls.append(img.size)
        out = img.convert("RGBA")
        out.putalpha(cutouts.read_mask(_mask_png(size=img.size)))
        return out

    monkeypatch.setattr(cutout_api, "photoroom_cutout", _cut)
    return calls


def test_the_cloud_fallback_needs_the_switch(client, photoroom):
    _draft(client, "dev-cloud-off")
    res = client.post("/api/cutout/cloud", data={
        "session_id": "dev-cloud-off", "name": "img_000.jpg"})
    assert res.status_code == 409
    assert client.charged == [] and photoroom == []


def test_the_cloud_fallback_is_paid_once_per_photo(client, photoroom):
    from backend.services import bg_removal
    bg_removal.activate(UID)
    _draft(client, "dev-cloud")
    sha = _source(client, "dev-cloud")
    res = client.post("/api/cutout/cloud", data={
        "session_id": "dev-cloud", "name": "img_000.jpg", "source_sha": sha})
    assert res.status_code == 200, res.text
    assert client.charged == [("image_ai", 1)] and len(photoroom) == 1
    row = client.db.cutout_get("dev-cloud", "img_000.jpg")
    assert row["state"] == "done" and row["provider"] == "photoroom"
    # Asked again for the same, unchanged photo: answered as it is.
    again = client.post("/api/cutout/cloud", data={
        "session_id": "dev-cloud", "name": "img_000.jpg"})
    assert again.status_code == 200 and again.json().get("reused") is True
    assert client.charged == [("image_ai", 1)] and len(photoroom) == 1


def test_a_cloud_cutout_that_failed_is_given_back(client, monkeypatch):
    from backend.services import bg_removal
    bg_removal.activate(UID)
    refunds = []
    monkeypatch.setattr(client.main, "_refund_cutouts",
                        lambda spent, held, photos=None: refunds.append(photos))

    def _down(_img):
        raise cutout_api.PhotoroomError("Photoroom is down — kept as shot.")

    monkeypatch.setattr(cutout_api, "photoroom_cutout", _down)
    _draft(client, "dev-cloud-down")
    res = client.post("/api/cutout/cloud", data={
        "session_id": "dev-cloud-down", "name": "img_000.jpg"})
    assert res.status_code == 422
    assert refunds == [None]
    assert client.db.cutout_get("dev-cloud-down", "img_000.jpg")["state"] == "failed"


# --- the app's Photoroom bill ----------------------------------------------------------------

class _Resp:
    def __init__(self, status=200, content=b""):
        self.status_code = status
        self.content = content
        self.headers = {}
        self.text = ""

    def json(self):
        return {}


def _cutout_png(size=(W, H)) -> bytes:
    img = Image.new("RGBA", size, (200, 30, 30, 0))
    ImageDraw.Draw(img).rectangle((50, 40, 150, 110), fill=(200, 30, 30, 255))
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture()
def wire(dbmod, monkeypatch):
    """The real photoroom_cutout over a stubbed HTTP call."""
    monkeypatch.setattr(config, "PHOTOROOM_API_KEY", "live-key")
    monkeypatch.setattr(config, "PHOTOROOM_ENABLED", True)
    monkeypatch.setattr(cutout_api.time, "sleep", lambda _s: None)
    sent = []

    def _segment(payload):
        sent.append(len(payload))
        return _Resp(200, _cutout_png())

    monkeypatch.setattr(cutout_api, "_segment", _segment)
    return sent


def test_photoroom_stops_at_the_monthly_budget(wire, monkeypatch):
    monkeypatch.setattr(config, "PHOTOROOM_COST_PER_IMAGE", 0.02)
    monkeypatch.setattr(config, "PHOTOROOM_MONTHLY_BUDGET_USD", 0.04)
    img = Image.open(BytesIO(_jpeg()))
    cutout_api.photoroom_cutout(img)
    cutout_api.photoroom_cutout(img)
    assert cutout_api.budget_exhausted()
    with pytest.raises(cutout_api.PhotoroomError) as err:
        cutout_api.photoroom_cutout(img)
    assert "paused until next month" in str(err.value)
    assert len(wire) == 2, "a call past the budget reached Photoroom"


def test_a_failed_call_is_not_counted_against_the_budget(dbmod, monkeypatch):
    monkeypatch.setattr(config, "PHOTOROOM_API_KEY", "live-key")
    monkeypatch.setattr(config, "PHOTOROOM_ENABLED", True)
    monkeypatch.setattr(cutout_api.time, "sleep", lambda _s: None)
    monkeypatch.setattr(cutout_api, "_segment", lambda _p: _Resp(402))
    with pytest.raises(cutout_api.PhotoroomError):
        cutout_api.photoroom_cutout(Image.open(BytesIO(_jpeg())))
    rows = dbmod.cutout_usage(time.strftime("%Y-%m", time.gmtime()))
    assert all(r["photos"] == 0 for r in rows if r["provider"] == "photoroom")


def test_the_kill_switch_stops_every_photoroom_call(wire, monkeypatch):
    monkeypatch.setattr(config, "PHOTOROOM_ENABLED", False)
    assert config.photoroom_ready() is False
    assert cutout_api.photoroom_cutout(Image.open(BytesIO(_jpeg()))) is None
    assert wire == []


def test_photoroom_calls_wait_for_a_free_slot(monkeypatch):
    monkeypatch.setattr(cutout_api, "_slots", threading.BoundedSemaphore(1))
    inside, peak = [0], [0]
    lock = threading.Lock()

    def _send():
        with lock:
            inside[0] += 1
            peak[0] = max(peak[0], inside[0])
        time.sleep(0.05)
        with lock:
            inside[0] -= 1
        return "ok"

    threads = [threading.Thread(target=cutout_api._in_slot, args=(_send,))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1


# --- the studio ---------------------------------------------------------------------------------

def test_the_studio_cuts_a_picture_by_its_edge(client, monkeypatch):
    """A painting the batch spared must not be lifted out of itself by the
    studio's button -- the art path, not Photoroom's cutout."""
    from backend.services import bg_removal
    bg_removal.activate(UID)
    opt = _draft(client, "dev-studio-art")
    img = Image.new("RGB", (W, H), (40, 40, 40))
    ImageDraw.Draw(img).rectangle((40, 30, 160, 120), fill=(230, 200, 120))
    img.save(opt / "img_000.jpg", "JPEG", quality=95)
    images.note_flags(opt / "img_000.jpg", art=True)
    res = client.post("/api/image/remove-bg", data={
        "session_id": "dev-studio-art", "name": "img_000.jpg"})
    assert res.status_code == 200, res.text
    assert res.json()["engine"] == "art"


def test_the_preview_writes_nothing(client):
    opt = _draft(client, "dev-preview")
    before = (opt / "img_000.jpg").read_bytes()
    res = client.post("/api/cutout/compose", data={
        "session_id": "dev-preview", "name": "img_000.jpg",
        "provider": "mlkit", "platform": "android"},
        files={"mask": ("m.png", _mask_png(), "image/png")})
    assert res.status_code == 200, res.text
    assert res.json()["image"].startswith("data:image/jpeg;base64,")
    assert (opt / "img_000.jpg").read_bytes() == before


def test_the_preview_can_be_transparent_or_any_colour(client):
    _draft(client, "dev-preview-2")
    clear = client.post("/api/cutout/compose", data={
        "session_id": "dev-preview-2", "name": "img_000.jpg",
        "background": "transparent"},
        files={"mask": ("m.png", _mask_png(), "image/png")})
    assert clear.json()["image"].startswith("data:image/png;base64,")
    blue = client.post("/api/cutout/compose", data={"background": "#0000ff"},
                       files={"mask": ("m.png", _mask_png(), "image/png"),
                              "file": ("c.jpg", _jpeg(), "image/jpeg")})
    assert blue.status_code == 200, blue.text
    import base64
    raw = base64.b64decode(blue.json()["image"].split(",", 1)[1])
    with Image.open(BytesIO(raw)) as im:
        r, g, b = im.convert("RGB").getpixel((2, 2))
        assert b > 200 and r < 40 and g < 40


# --- export and colour ---------------------------------------------------------------------------

def test_a_cut_photo_can_be_downloaded_transparent(client):
    _draft(client, "dev-png")
    assert _apply(client, "dev-png", _source(client, "dev-png")).status_code == 200
    res = client.get("/api/image/cutout/dev-png/img_000.jpg")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    with Image.open(BytesIO(res.content)) as im:
        assert im.mode == "RGBA" and im.getpixel((2, 2))[3] == 0


def test_a_photo_that_was_never_cut_has_nothing_to_download(client):
    _draft(client, "dev-png-none")
    assert client.get("/api/image/cutout/dev-png-none/img_000.jpg").status_code == 409


def test_the_sellers_colour_is_saved_checked_and_used(client):
    assert client.post("/api/prefs", json={"cutout_background": "not a colour",
                                           "quantity": 1}).status_code == 200
    assert "cutout_background" not in client.db.get_prefs(UID)
    client.post("/api/prefs", json={"cutout_background": "#FF0000"})
    assert client.db.get_prefs(UID)["cutout_background"] == "#ff0000"
    opt = _draft(client, "dev-colour")
    assert _apply(client, "dev-colour", _source(client, "dev-colour")).status_code == 200
    with Image.open(opt / "img_000.jpg") as im:
        r, g, b = im.convert("RGB").getpixel((3, 3))
        assert r > 230 and g < 30 and b < 30


# --- what the device may do ---------------------------------------------------------------------

def test_what_the_device_may_do_is_the_servers_to_say(client, monkeypatch):
    monkeypatch.setattr(config, "BG_LOCAL_PROVIDERS", ("ios", "android", "web"))
    monkeypatch.setattr(config, "BG_WEB_MODEL_URL", "")
    local = client.get("/api/bg-removal/status").json()["local"]
    assert local["providers"] == ["ios", "android"], \
        "the browser engine was offered with no model to load"
    monkeypatch.setattr(config, "BG_WEB_MODEL_URL", "https://cdn.example/m.onnx")
    monkeypatch.setattr(config, "BG_WEB_MODEL_SHA256", "a" * 64)
    local = client.get("/api/bg-removal/status").json()["local"]
    assert local["providers"] == ["ios", "android", "web"]
    assert local["web_model"]["sha256"] == "a" * 64
    assert local["timeout_ms"] >= 5000


def test_nothing_runs_on_the_device_by_default(fresh_config):
    """Every engine is switched on by the operator, deliberately -- and the
    browser's only once its model's licence is signed off."""
    cfg = fresh_config()
    assert cfg.BG_LOCAL_PROVIDERS == ()
    assert cfg.PHOTOROOM_ENABLED is True
    assert cfg.photoroom_budget_photos() is None
    cfg = fresh_config(BG_LOCAL_PROVIDERS="ios, android, toaster",
                       PHOTOROOM_MONTHLY_BUDGET_USD="$50",
                       PHOTOROOM_COST_PER_IMAGE="0.02")
    assert cfg.BG_LOCAL_PROVIDERS == ("ios", "android")
    assert cfg.photoroom_budget_photos() == 2500


def test_an_items_session_inherits_the_piles_queue_and_photo_extras(client):
    """The split: each item's photos are copied out of the staging pile into
    a session of its own, and what waits for the device goes with them --
    under the item's names -- along with what the screen said and anything a
    cut already kept beside them."""
    main, db = client.main, client.db
    pile = storage.optimized_dir("dev-pile")
    names = ["img_000.jpg", "img_001.jpg", "img_002.jpg"]
    for n in names:
        (pile / n).write_bytes(_jpeg())
    images.note_flags(pile / "img_002.jpg", art=True)
    db.cutout_mark([{"session_id": "dev-pile", "name": "img_001.jpg"},
                    {"session_id": "dev-pile", "name": "img_002.jpg",
                     "kind": "art"}], UID)
    ctx = main._BulkContext(
        uid=UID, prefs={}, strategy=None, auto_promote=False, billing=False,
        notes="", names=names,
        groups=[{"name": "a", "indices": [0]}, {"name": "b", "indices": [1, 2]}],
        opt_dir=pile, staging_id="dev-pile")
    sid, item_names, charged = main._bulk_item_session(1, ctx)
    assert item_names == ["img_000.jpg", "img_001.jpg"] and charged is False
    assert db.cutout_get(sid, "img_000.jpg")["state"] == "pending"
    assert db.cutout_get(sid, "img_001.jpg")["kind"] == "art"
    assert images.photo_flags(storage.optimized_dir(sid) / "img_001.jpg")["art"]

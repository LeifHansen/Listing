"""A listing takes a video, and eBay's own limit decides how many.

The ask was "import as many videos as eBay allows, just the upload, no
editing". eBay allows ONE per listing — it says so in its seller help and
enforces it by IGNORING the extras rather than refusing them, which is the
failure mode worth testing against: a seller who could add three would see
three uploads succeed and a listing with one video, and nothing anywhere
would say which one.

So the number is a constant (models.MAX_VIDEOS) rather than a habit, the
plumbing either side of it carries a list so the day eBay raises the limit is
a one-line change, and the route refuses the second video rather than
accepting it into a record eBay will silently trim.

The other half is that a video is not a photo. Photos are PULLED — the
publish hands eBay a URL and eBay fetches it — and a video has no such URL:
it is pushed through the Media API, moderated by eBay for up to 48 hours, and
referenced from the listing only by the id eBay minted. Everything below
follows from that: why the upload returns before eBay has seen the file, why
the listing carries an id and a status and not just a filename, why a stale
save may not erase them, and why a publish is never failed over a video.
"""
from __future__ import annotations

import io

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend.models import MAX_VIDEOS, Listing  # noqa: E402
from backend.services import ebay_video  # noqa: E402


# --- an MP4 the probe can actually read -------------------------------------

def _box(kind: bytes, payload: bytes) -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


def mp4_bytes(seconds: float = 12.0, brand: bytes = b"isom",
              compatible: bytes = b"", pad: int = 0) -> bytes:
    """The smallest thing that is honestly an MP4: an ftyp box declaring the
    brand, and a moov/mvhd carrying a timescale and a duration. `pad` adds
    free space so a test can make a file any size it likes.

    `compatible` is the brands list eBay's reader would consult after the
    major brand; it defaults to the major brand alone, which is what a real
    QuickTime export writes."""
    ftyp = _box(b"ftyp", brand + b"\x00\x00\x02\x00" + (compatible or brand))
    timescale = 1000
    mvhd = _box(b"mvhd", b"\x00" + b"\x00" * 3          # version 0 + flags
                + b"\x00" * 8                            # created / modified
                + timescale.to_bytes(4, "big")
                + int(seconds * timescale).to_bytes(4, "big")
                + b"\x00" * 80)
    moov = _box(b"moov", mvhd)
    free = _box(b"free", b"\x00" * pad) if pad else b""
    return ftyp + free + moov


def test_the_sample_is_a_real_mp4_the_probe_can_read(tmp_path):
    """The fixture above has to be trusted by everything below it."""
    path = tmp_path / "v.mp4"
    path.write_bytes(mp4_bytes(seconds=12.0))
    facts = ebay_video.probe(path)
    assert facts["mp4"] is True, facts
    assert facts["brand"] == "isom"
    assert facts["seconds"] == pytest.approx(12.0, abs=0.01)


# --- what the app refuses before eBay would ---------------------------------

def test_a_mov_renamed_to_mp4_is_refused_here_not_in_48_hours(tmp_path):
    """The common way to fail eBay's format rule, and the expensive one.

    eBay only takes MP4. It does not say so at upload — the video is
    accepted, queued, and refused by moderation up to two days later, with
    the seller long gone and the listing live without a video. The brand is
    in the first twelve bytes of the file, so this costs a millisecond."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(mp4_bytes(brand=b"qt  "))  # a real .mov export

    refusal = ebay_video.check(path)

    assert refusal, "a QuickTime file passed as MP4"
    assert "mp4" in refusal.lower()
    assert "qt" in refusal, "the seller is not told what they actually have"


def test_a_video_over_ebays_limit_is_refused_with_its_own_size(tmp_path):
    path = tmp_path / "big.mp4"
    path.write_bytes(mp4_bytes(pad=16))
    # Stat, rather than writing 150MB of zeroes into the test volume.
    real_size = ebay_video.MAX_VIDEO_BYTES + 1

    class _Stat:
        st_size = real_size

    original = type(path).stat
    try:
        type(path).stat = lambda self, **kw: _Stat()  # noqa: ARG005
        refusal = ebay_video.check(path)
    finally:
        type(path).stat = original

    assert refusal and "150MB" in refusal, refusal


def test_a_video_over_a_minute_is_refused(tmp_path):
    path = tmp_path / "long.mp4"
    path.write_bytes(mp4_bytes(seconds=95.0))
    refusal = ebay_video.check(path)
    assert refusal and "one minute" in refusal, refusal


def test_a_video_the_probe_cannot_read_is_left_to_ebay(tmp_path):
    """Erring towards eBay, deliberately.

    A local reader that refuses a file eBay would have taken is the worse of
    the two errors: the seller cannot argue with it and has no way to find
    out it was wrong. An unreadable duration is reported as unknown and the
    file goes up."""
    path = tmp_path / "odd.mp4"
    # A valid ftyp and nothing else — no moov, so no duration to be had.
    path.write_bytes(_box(b"ftyp", b"isom" + b"\x00" * 4 + b"isom"))
    facts = ebay_video.probe(path)
    assert facts["mp4"] is True and facts["seconds"] is None
    assert ebay_video.check(path) is None


def test_a_probe_never_raises_on_rubbish(tmp_path):
    path = tmp_path / "not-a-video.mp4"
    path.write_bytes(b"\xff" * 64)
    facts = ebay_video.probe(path)          # must not raise
    assert facts["mp4"] is False
    assert "MP4" in (ebay_video.check(path) or "")


# --- the model holds eBay's ceiling -----------------------------------------

def test_the_model_caps_videos_at_what_ebay_allows():
    """Held at the model because every write path lands on it — the upload
    route, a save from the editor, an import, a client that never enforced
    it. Past MAX_VIDEOS eBay ignores the extras instead of refusing them."""
    listing = Listing(videos=[{"file": f"video_{i}.mp4"} for i in range(5)])
    assert len(listing.videos) == MAX_VIDEOS
    assert MAX_VIDEOS == ebay_video.MAX_VIDEOS_PER_LISTING, (
        "two copies of eBay's limit that can disagree")


def test_a_video_entry_that_names_no_video_is_dropped():
    listing = Listing(videos=[{"status": "PROCESSING"}, {}])
    assert listing.videos == []


def test_a_video_carries_ebays_id_and_status_not_just_a_filename():
    """The three facts that cannot be recovered if lost: which file, which id
    eBay gave it, and what eBay last said about it. Losing the id costs a
    second 150MB upload; losing the status leaves a blocked video looking
    like one that is simply still being reviewed."""
    listing = Listing(videos=[{"file": "video_1.mp4", "ebay_video_id": "7",
                               "status": "PROCESSING", "size": 4096}])
    v = listing.videos[0]
    assert (v.file, v.ebay_video_id, v.status, v.size) == (
        "video_1.mp4", "7", "PROCESSING", 4096)


# --- what reaches eBay ------------------------------------------------------

def test_the_trading_request_names_the_video_by_ebays_id():
    from backend.services import ebay_trading

    listing = Listing(title="Tee", videos=[
        {"file": "v.mp4", "ebay_video_id": "9001", "status": "LIVE"}])
    xml = "".join(ebay_trading._item_fields(listing))

    assert "<VideoDetails><VideoID>9001</VideoID></VideoDetails>" in xml


def test_a_video_ebay_refused_is_never_sent_back_to_it():
    """A BLOCKED video still has an id, and naming it rejects the whole
    publish — so the listing would not go live at all over a video the seller
    could have simply removed."""
    from backend.services import ebay_trading

    for status in (ebay_video.STATUS_BLOCKED, ebay_video.STATUS_FAILED):
        listing = Listing(title="Tee", videos=[
            {"file": "v.mp4", "ebay_video_id": "9001", "status": status}])
        assert ebay_trading.video_ids(listing) == [], status
        assert "VideoDetails" not in "".join(ebay_trading._item_fields(listing))


def test_a_video_still_in_moderation_goes_out_with_the_listing():
    """eBay attaches a PROCESSING video and shows it when moderation clears.
    Holding the publish back for it would be this app inventing a rule eBay
    does not have — and a wait of up to 48 hours."""
    from backend.services import ebay_trading

    listing = Listing(title="Tee", videos=[
        {"file": "v.mp4", "ebay_video_id": "42", "status": "PROCESSING"}])
    assert ebay_trading.video_ids(listing) == ["42"]


def test_a_video_with_no_ebay_id_yet_sends_no_video_details():
    """There is no <VideoURL>: a file eBay has not been handed cannot be named
    in the request at all."""
    from backend.services import ebay_trading

    listing = Listing(title="Tee", videos=[{"file": "v.mp4"}])
    assert ebay_trading.video_ids(listing) == []
    assert "VideoDetails" not in "".join(ebay_trading._item_fields(listing))


def test_an_unrelated_revise_does_not_carry_the_video():
    """<VideoDetails> REPLACES the listing's video set, exactly as
    PictureDetails does for photos. Sending it on a price edit is how a video
    added in Seller Hub would quietly disappear."""
    from backend.services import ebay_trading

    listing = Listing(title="Tee", price=10.0, videos=[
        {"file": "v.mp4", "ebay_video_id": "9001", "status": "LIVE"}])
    _call, body = ebay_trading.build_revise_item(listing, "1234")
    assert "VideoDetails" not in body

    listing.mark_dirty("videos")
    _call, body = ebay_trading.build_revise_item(listing, "1234")
    assert "<VideoID>9001</VideoID>" in body, (
        "adding a video to a live listing never reached eBay")


def test_a_video_edit_is_one_a_revise_can_actually_carry():
    from backend.services import dirty_fields, ebay_trading

    assert "videos" in dirty_fields.TRACKED
    assert "videos" in ebay_trading.REVISABLE_FIELDS
    assert ebay_trading.unsendable_revise_fields(
        Listing(dirty_fields=["videos"])) == []


def test_ebays_own_video_is_kept_when_a_listing_is_imported():
    """Without this an import reads as "no video", and the first revise
    carrying <VideoDetails> removes one the seller added on eBay."""
    from xml.etree import ElementTree as ET

    from backend.services import ebay_trading

    item = ET.fromstring(
        "<Item><Title>Tee</Title><VideoDetails><VideoID>5150</VideoID>"
        "</VideoDetails></Item>")
    data = ebay_trading._item_to_listing(item)
    assert data["videos"] == [{"ebay_video_id": "5150", "status": "LIVE"}]
    assert ebay_trading.video_ids(Listing(**data)) == ["5150"]


def test_moderation_moving_on_is_not_read_as_a_sellers_edit():
    """eBay's status changes under us, minutes or days after the save that
    put the file here. Compared as an edit it would mark the field dirty on
    every status poll — putting <VideoDetails> into every unrelated revise
    and showing unsaved changes nobody made."""
    from backend.services import dirty_fields

    stored = {"videos": [{"file": "v.mp4", "ebay_video_id": "1",
                          "status": "PROCESSING"}]}
    same = Listing(videos=[{"file": "v.mp4", "ebay_video_id": "1",
                            "status": "LIVE", "message": "ok"}])
    assert "videos" not in dirty_fields.changed_fields(same, stored)

    swapped = Listing(videos=[{"file": "other.mp4"}])
    assert "videos" in dirty_fields.changed_fields(swapped, stored)


def test_the_two_sides_describe_one_video_the_same_way():
    """This app holds a file, a size and a status; eBay reports an id. The id
    is the only thing both can say, so it is what a comparison uses —
    otherwise every record with a video reads as permanently edited against
    its own sync shadow, and rides along on every unrelated revise."""
    from backend.services import dirty_fields

    ours = [{"file": "v.mp4", "ebay_video_id": "77", "status": "PROCESSING",
             "size": 900}]
    ebays = [{"file": "", "ebay_video_id": "77", "status": "LIVE"}]
    assert (dirty_fields.comparable_field("videos", ours)
            == dirty_fields.comparable_field("videos", ebays))

    # Until eBay has given one, the file is the identity — so swapping the
    # file before the upload lands is still an edit.
    assert (dirty_fields.comparable_field("videos", [{"file": "a.mp4"}])
            != dirty_fields.comparable_field("videos", [{"file": "b.mp4"}]))


def test_a_sync_never_reconciles_the_video():
    """GetItem reports a video only once moderation has passed it, so for up
    to 48 hours eBay's answer to "what videos does this listing have" is
    "none". Merged against that, a video on its way up is deleted — id, local
    file and all — and a video both sides DO agree on comes back in eBay's
    shape, without the file the editor plays it from."""
    from backend.services import sync_merge

    assert "videos" in sync_merge.TRACKED, (
        "it has to be tracked, or a revise could never carry it")
    assert "videos" in sync_merge.NOT_RECONCILED

    local = Listing(title="Tee", videos=[
        {"file": "v.mp4", "ebay_video_id": "77", "status": "PROCESSING",
         "size": 900}])
    shadow = {"title": "Tee", "videos": []}
    remote = {"title": "Tee", "videos": []}   # eBay is still moderating

    merged = sync_merge.three_way(local, shadow, remote)

    assert [v.model_dump() for v in merged.listing.videos] == [
        {"file": "v.mp4", "ebay_video_id": "77", "status": "PROCESSING",
         "message": "", "size": 900}]
    assert "videos" not in merged.conflicts
    # And it is left out of the base too — a shadow nothing compares against
    # is noise the next reader has to work out is unused.
    assert "videos" not in sync_merge.shadow_from(
        {"title": "Tee", "videos": [{"ebay_video_id": "77"}]})


# --- the Media API calls ----------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, json_body=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}
        self.headers = headers or {}
        self.text = text or ""

    def json(self):
        return self._json


def test_the_video_id_is_read_from_the_location_header(monkeypatch):
    """eBay answers createVideo 201 with an EMPTY BODY and the id only in
    Location. A body-first read gets None, and every later call goes to
    /video/None."""
    seen = {}

    def fake_post(url, **kw):
        seen["url"] = url
        seen["json"] = kw.get("json")
        return FakeResponse(201, headers={
            "location": "https://apim.ebay.com/commerce/media/v1_beta/video/8899"})

    monkeypatch.setattr(ebay_video.httpx, "post", fake_post)

    assert ebay_video.create_video("tok", "Vintage tee", 12345) == "8899"
    assert seen["url"].endswith("/commerce/media/v1_beta/video")
    assert seen["json"]["classification"] == ["ITEM"], (
        "only ITEM makes a video attachable to a listing")
    assert seen["json"]["size"] == 12345, (
        "eBay matches this against the bytes uploaded next")


def test_the_upload_streams_the_file_rather_than_reading_it(monkeypatch, tmp_path):
    """150MB awaited into a bytes object, on the box that also holds a 176MB
    cutout model, is an OOM waiting for two sellers at once."""
    path = tmp_path / "v.mp4"
    path.write_bytes(mp4_bytes(pad=2048))
    seen = {}

    def fake_post(url, **kw):
        seen["url"] = url
        seen["headers"] = kw.get("headers")
        seen["content"] = kw.get("content")
        return FakeResponse(200)

    monkeypatch.setattr(ebay_video.httpx, "post", fake_post)
    ebay_video.upload_video("tok", "8899", path)

    assert seen["url"].endswith("/video/8899/upload")
    assert seen["headers"]["Content-Type"] == "application/octet-stream"
    assert hasattr(seen["content"], "read"), (
        "the whole file was read into memory instead of being streamed")


def test_ebays_reason_reaches_the_seller_not_a_status_code(monkeypatch):
    monkeypatch.setattr(ebay_video.httpx, "post", lambda url, **kw: FakeResponse(
        400, json_body={"errors": [{"longMessage": "The video is too long."}]},
        text='{"errors":[]}'))
    with pytest.raises(ebay_video.VideoError) as exc:
        ebay_video.create_video("tok", "t", 1)
    assert "too long" in str(exc.value)


def test_an_app_without_media_access_is_not_reported_as_a_bad_video(monkeypatch):
    """The Media API is not open to every keyset. That is the app's problem,
    and telling a seller their file was rejected over it sends them off to
    re-shoot a video that was fine."""
    monkeypatch.setattr(ebay_video.httpx, "post", lambda url, **kw: FakeResponse(
        403, text="Insufficient permissions to fulfill the request."))
    with pytest.raises(ebay_video.VideoNotSupported):
        ebay_video.create_video("tok", "t", 1)


def test_a_lost_status_read_does_not_undo_an_upload_that_worked(monkeypatch,
                                                                tmp_path):
    """The bytes are on eBay; only the question afterwards failed. Reporting
    that as a failure has the seller send 150MB again."""
    path = tmp_path / "v.mp4"
    path.write_bytes(mp4_bytes())
    monkeypatch.setattr(ebay_video.httpx, "post", lambda url, **kw: FakeResponse(
        201, headers={"location": "/video/77"}))
    monkeypatch.setattr(ebay_video, "get_video", lambda *a, **k: (_ for _ in ()).throw(
        ebay_video.VideoError("eBay is down")))

    out = ebay_video.send_to_ebay("tok", path)
    assert out["video_id"] == "77"
    assert out["status"] == ebay_video.STATUS_PROCESSING


# --- the routes -------------------------------------------------------------

class FakeDb:
    """The parts of backend.db the video routes touch."""

    UNAVAILABLE = object()

    def __init__(self, rows=None):
        self.rows = rows or {}

    def enabled(self):
        return True

    def get_listing(self, rid):
        return self.rows.get(rid)

    def mutate_listing_data(self, rid, mutate, status="", user_id=None):
        rec = self.rows.get(rid)
        if rec is None:
            return None
        rec["listing"] = mutate(dict(rec.get("listing") or {}))
        return rec["listing"]

    def upsert_listing(self, rid, data, status="", user_id=None, when=None):
        self.rows.setdefault(rid, {"id": rid})["listing"] = data
        return True

    def __getattr__(self, name):
        raise AttributeError(name)


@pytest.fixture
def api(monkeypatch, tmp_path):
    from backend import main

    fake = FakeDb(rows={"s1": {"id": "s1", "status": "draft",
                               "listing": {"title": "Vintage tee"}}})
    monkeypatch.setattr(main, "db", fake)
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main, "_uid", lambda *a, **k: "u1")
    monkeypatch.setattr(main, "_in_background", lambda fn, *a, **k: None)
    monkeypatch.setattr(main.storage, "save_listing", lambda *a, **k: None)
    monkeypatch.setattr(main.storage, "load_listing", lambda sid: None)
    monkeypatch.setattr(main.config, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(main.storage.config, "SESSIONS_DIR", tmp_path / "sessions")
    return main, TestClient(main.app), fake


def _upload(client, data=None, name="clip.mp4"):
    return client.post(
        "/api/listings/s1/video",
        files={"file": (name, io.BytesIO(data if data is not None else mp4_bytes()),
                        "video/mp4")})


def test_the_video_lands_on_the_listing_and_the_request_does_not_wait_for_ebay(api):
    """The upload returns the moment the file is stored. eBay's Media API and
    its 48-hour moderation queue are behind it — the same rule "Add photos"
    follows for its optimize pass, and for the same reason."""
    main, client, fake = api

    res = _upload(client)

    assert res.status_code == 200, res.text
    videos = res.json()["videos"]
    assert len(videos) == 1
    assert videos[0]["file"].endswith(".mp4")
    assert videos[0]["on_ebay"] is False
    assert videos[0]["url"] == f"/media/s1/video/{videos[0]['file']}"
    assert fake.rows["s1"]["listing"]["videos"][0]["file"] == videos[0]["file"]


def test_a_second_video_is_refused_rather_than_silently_dropped(api):
    """eBay ignores the extras rather than refusing them, so accepting one
    here would mean an upload that succeeded and a listing without it."""
    main, client, _fake = api
    assert _upload(client).status_code == 200

    res = _upload(client)

    assert res.status_code == 400, res.text
    assert str(MAX_VIDEOS) in res.text and "ebay allows" in res.text.lower()


def test_a_file_that_is_not_mp4_never_reaches_the_disk(api, tmp_path):
    main, client, fake = api
    res = client.post("/api/listings/s1/video",
                      files={"file": ("clip.mov", io.BytesIO(mp4_bytes()),
                                      "video/quicktime")})
    assert res.status_code == 400
    assert "mp4" in res.text.lower()
    assert not fake.rows["s1"]["listing"].get("videos")


def test_a_video_that_only_ebay_would_refuse_is_refused_here_and_removed(api):
    """Named .mp4, actually QuickTime. Nothing is left behind on the volume:
    a rejected upload that kept its 150MB would be worse than the upload."""
    main, client, fake = api
    res = _upload(client, data=mp4_bytes(brand=b"qt  "))
    assert res.status_code == 400 and "mp4" in res.text.lower()
    assert not fake.rows["s1"]["listing"].get("videos")
    assert main.storage.list_videos("s1") == []


def test_removing_a_video_takes_it_off_the_listing_and_the_disk(api):
    main, client, fake = api
    name = _upload(client).json()["videos"][0]["file"]
    assert main.storage.list_videos("s1") == [name]

    res = client.delete(f"/api/listings/s1/video/{name}")

    assert res.status_code == 200, res.text
    assert res.json()["videos"] == []
    assert fake.rows["s1"]["listing"]["videos"] == []
    assert main.storage.list_videos("s1") == []


def test_a_video_name_from_the_client_cannot_escape_the_session(api):
    """The name is minted by the route and never taken from the client — but
    it travels in a public /media URL and comes back as a path segment, so it
    is checked on the way out as well as on the way in."""
    from backend import storage

    main, client, _fake = api
    for bad in ("../secrets.mp4", "..", "a/b.mp4", "notes.txt", ""):
        with pytest.raises(ValueError):
            storage.safe_video_name(bad)
    assert storage.safe_video_name("video_1789.mp4") == "video_1789.mp4"

    assert client.delete("/api/listings/s1/video/notes.txt").status_code == 400
    assert client.get("/media/s1/video/notes.txt").status_code == 404


def test_the_status_route_says_what_ebay_is_doing_in_words(api):
    """"PROCESSING" is not written for a seller. The gap between "eBay is
    reviewing this" and "eBay refused it" is the difference between waiting
    and re-shooting."""
    main, client, fake = api
    _upload(client)
    fake.rows["s1"]["listing"]["videos"][0].update(
        {"ebay_video_id": "5", "status": "PROCESSING"})

    body = client.get("/api/listings/s1/video").json()

    assert body["max_videos"] == MAX_VIDEOS
    assert body["videos"][0]["on_ebay"] is True
    assert "48 hours" in body["videos"][0]["note"]


def test_the_editor_can_play_a_stored_video(api):
    main, client, _fake = api
    name = _upload(client).json()["videos"][0]["file"]

    res = client.get(f"/media/s1/video/{name}")

    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "video/mp4"


def test_a_browser_can_play_a_video_at_all(api):
    """default-src 'self' covers media, so without an explicit media-src the
    two places a video is ever played from — the R2 bucket the /media route
    redirects to, and the blob: URL of a just-picked file — are blocked. The
    symptom is silent: a player that shows a frame and never starts."""
    main, _client, _fake = api
    directive = next(d for d in main._CSP.split("; ") if d.startswith("media-src"))
    assert "https:" in directive and "blob:" in directive


# --- what a stale tab may not undo ------------------------------------------

def test_a_stale_save_cannot_erase_the_id_ebay_gave_a_video():
    """`videos` cannot be blanket-protected — removing a video IS a save that
    omits it — so the merge is per video and keyed on the file. A tab that
    loaded before the upload job stamped the id would otherwise cost the
    seller a second 150MB upload, and a publish in between would go out with
    no video at all."""
    from backend.marketplaces import state

    stored = {"videos": [{"file": "v.mp4", "ebay_video_id": "77",
                          "status": "PROCESSING", "size": 900}]}
    incoming = Listing(videos=[{"file": "v.mp4"}])

    assert state.restore_video_state(incoming, stored) is True
    assert incoming.videos[0].ebay_video_id == "77"
    assert incoming.videos[0].status == "PROCESSING"
    assert incoming.videos[0].size == 900


def test_removing_a_video_still_removes_it():
    """The other half of the same rule: an entry the client dropped is
    dropped, because that is the seller deleting it."""
    from backend.marketplaces import state

    stored = {"videos": [{"file": "v.mp4", "ebay_video_id": "77"}]}
    incoming = Listing(videos=[])
    state.restore_video_state(incoming, stored)
    assert incoming.videos == []


# --- the publish backstop ---------------------------------------------------

def test_a_video_added_before_ebay_was_connected_still_goes_up(monkeypatch,
                                                               tmp_path):
    """The ordinary path uploads minutes after the seller picks the file. This
    is the case it cannot cover: a draft started before eBay was linked."""
    from backend.services import listing_sync

    monkeypatch.setattr(listing_sync, "video_file_for",
                        lambda sid, name: str(tmp_path / name))
    (tmp_path / "v.mp4").write_bytes(mp4_bytes())
    monkeypatch.setattr(listing_sync.ebay_video, "send_to_ebay",
                        lambda *a, **k: {"video_id": "31", "status": "PROCESSING",
                                         "message": ""})

    listing = Listing(title="Tee", videos=[{"file": "v.mp4"}])
    assert listing_sync.push_videos("tok", "s1", listing) is True
    assert listing.videos[0].ebay_video_id == "31"


def test_a_video_is_streamed_back_from_r2_rather_than_held_in_memory(monkeypatch,
                                                                     tmp_path):
    """A video older than a few hours lives in the bucket — the reclaim pass
    frees the local copy, and on a 1GB volume that is the normal state, not a
    missing video. Fetching it back through restore() would hold all 150MB in
    a bytes on the box that is also holding a 176MB cutout model."""
    from backend import objstore
    from backend.services import listing_sync

    monkeypatch.setattr(listing_sync.storage, "video_path",
                        lambda sid: tmp_path / "gone")
    monkeypatch.setattr(listing_sync.storage, "video_dir",
                        lambda sid: tmp_path)
    monkeypatch.setattr(objstore, "enabled", lambda: True)
    monkeypatch.setattr(objstore, "restore", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("read the whole video into memory")))
    fetched = []
    monkeypatch.setattr(objstore, "download",
                        lambda key, path: fetched.append(key) or True)

    path = listing_sync.video_file_for("s1", "v.mp4")

    assert path == str(tmp_path / "v.mp4")
    assert fetched == ["sessions/s1/video/v.mp4"]


def test_a_video_already_on_ebay_is_not_uploaded_twice(monkeypatch):
    from backend.services import listing_sync

    def _explode(*a, **k):
        raise AssertionError("re-uploaded a video eBay already has")

    monkeypatch.setattr(listing_sync.ebay_video, "send_to_ebay", _explode)
    listing = Listing(videos=[{"file": "v.mp4", "ebay_video_id": "31",
                               "status": "LIVE"}])
    assert listing_sync.push_videos("tok", "s1", listing) is False


def test_a_failed_video_upload_never_fails_the_publish(monkeypatch, tmp_path):
    """A listing with no video sells; a listing that will not publish is the
    seller's afternoon. The failure is recorded on the video, where the editor
    shows it, and the next publish tries again."""
    from backend.services import listing_sync

    (tmp_path / "v.mp4").write_bytes(mp4_bytes())
    monkeypatch.setattr(listing_sync, "video_file_for",
                        lambda sid, name: str(tmp_path / name))
    monkeypatch.setattr(
        listing_sync.ebay_video, "send_to_ebay",
        lambda *a, **k: (_ for _ in ()).throw(ebay_video.VideoError("eBay is down")))

    listing = Listing(title="Tee", videos=[{"file": "v.mp4"}])
    assert listing_sync.push_videos("tok", "s1", listing) is True   # no raise
    assert listing.videos[0].status == ebay_video.STATUS_FAILED
    assert "down" in listing.videos[0].message


def test_a_keyset_without_media_access_leaves_the_video_alone(monkeypatch,
                                                              tmp_path):
    """Nothing tells the seller their video was rejected, and the file stays
    put so it goes up the day eBay enables the app."""
    from backend.services import listing_sync

    (tmp_path / "v.mp4").write_bytes(mp4_bytes())
    monkeypatch.setattr(listing_sync, "video_file_for",
                        lambda sid, name: str(tmp_path / name))
    monkeypatch.setattr(
        listing_sync.ebay_video, "send_to_ebay",
        lambda *a, **k: (_ for _ in ()).throw(
            ebay_video.VideoNotSupported("not open yet")))

    listing = Listing(title="Tee", videos=[{"file": "v.mp4"}])
    assert listing_sync.push_videos("tok", "s1", listing) is False
    assert listing.videos[0].status == ""
    assert listing.videos[0].file == "v.mp4"


def test_moderation_is_asked_about_only_while_it_is_still_running(monkeypatch):
    """A terminal status is never asked about again — otherwise an open tab
    becomes a request per second for the life of the listing."""
    from backend.services import listing_sync

    asked = []

    def fake_get(_token, video_id):
        asked.append(video_id)
        return {"status": "LIVE", "message": "", "play_url": ""}

    monkeypatch.setattr(listing_sync.ebay_video, "get_video", fake_get)
    listing = Listing(videos=[{"file": "a.mp4", "ebay_video_id": "1",
                               "status": "PROCESSING"}])
    assert listing_sync.refresh_video_status("tok", listing) is True
    assert asked == ["1"]

    assert listing_sync.refresh_video_status("tok", listing) is False
    assert asked == ["1"], "asked eBay about a video it had already answered for"


def test_the_media_api_is_not_pointed_at_the_ordinary_ebay_host():
    """The one eBay API that does not live on api.ebay.com. Pointing these
    calls at EBAY_API_BASE gets a 404 that reads exactly like a bad path."""
    from backend import config

    assert "apim." in config.EBAY_MEDIA_BASE
    assert config.EBAY_MEDIA_BASE.endswith("/commerce/media/v1_beta")


def test_videos_need_no_new_permission_from_sellers_who_already_connected():
    """The Media API runs on sell.inventory, which every connected seller
    already granted. A new scope would mean every one of them reconnecting
    before they could add a video."""
    from backend import config

    assert ("https://api.ebay.com/oauth/api_scope/sell.inventory"
            in config.EBAY_OAUTH_SCOPES)

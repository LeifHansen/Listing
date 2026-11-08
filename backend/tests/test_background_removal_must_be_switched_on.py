"""Background removal is switched on by each seller, and counted.

Photoroom cuts the backgrounds, on the app's own account: it has no API for
creating an account or a key on a seller's behalf, so there is no linked
account to make for each seller. What each seller has instead is a switch,
and it starts OFF. Switching it on (Settings -> Background removal) is where
they agree to the photos they choose being sent to Photoroom, so until they
do, no photo of theirs may reach Photoroom by any route -- the single
upload, the pipeline, Add photos, the bulk pile, or the studio.

And because every cutout is on the app's bill, each seller's month is
counted against BG_REMOVAL_MONTHLY_LIMIT: reserved before the token charge,
given back for every photo that was never cut, exactly where the token
refund for that photo happens.

What is pinned here:

  * the switch is off until switched on, and /api/prefs cannot flip it past
    the consent the Settings card asks for;
  * every route that cuts refuses a seller who has not switched it on, with a
    409 and nothing charged;
  * the month's count goes up for what was cut, back down for what was not,
    and refuses (429, nothing counted) what would go past the limit;
  * a counter that cannot be read fails open, as the token charge does;
  * with no database there are no accounts: the install is the operator's,
    and the key alone switches it on;
  * deleting an account deletes its count.
"""
from __future__ import annotations

from io import BytesIO

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from backend import config  # noqa: E402

UID = "u-bg"


def _jpeg() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 48), (200, 180, 160)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture()
def keyed(monkeypatch):
    monkeypatch.setattr(config, "PHOTOROOM_API_KEY", "live-key")
    monkeypatch.setattr(config, "PHOTOROOM_SANDBOX", False)
    monkeypatch.setattr(config, "BG_REMOVAL_MONTHLY_LIMIT", 10)


@pytest.fixture()
def seller(dbmod, keyed):
    """A real (SQLite) database with one seller in it."""
    assert dbmod.create_user(UID, "bg@example.com", "x")
    from backend.services import bg_removal
    return bg_removal


@pytest.fixture()
def client(seller, monkeypatch):
    from backend import main
    monkeypatch.setattr(main.deps, "uid", lambda request: UID)
    monkeypatch.setattr(main.auth, "current_user", lambda request: {"id": UID})
    charged: list = []

    def _charge(request, feature, units=1):
        charged.append((feature, units))
        return None

    monkeypatch.setattr(main.deps, "charge_ai", _charge)
    c = TestClient(main.app)
    c.charged = charged
    c.main = main
    return c


# --- the switch --------------------------------------------------------------

def test_it_starts_off(seller):
    status = seller.status(UID)
    assert status["available"] is True
    assert status["needs_activation"] is True
    assert status["active"] is False


def test_switching_it_on_records_the_consent(seller, dbmod):
    status = seller.activate(UID)
    assert status["active"] is True
    rec = dbmod.get_prefs(UID)[seller.PREF_KEY]
    assert rec["active"] == 1
    assert rec["consent"] == seller.CONSENT
    assert rec["activated_at"]


def test_switching_it_off_stops_it(seller):
    seller.activate(UID)
    assert seller.deactivate(UID)["active"] is False
    with pytest.raises(seller.Refused) as err:
        seller.check(UID)
    assert err.value.status == 409


def test_there_is_nothing_to_switch_on_without_a_key(seller, monkeypatch):
    monkeypatch.setattr(config, "PHOTOROOM_API_KEY", "")
    with pytest.raises(seller.Refused) as err:
        seller.activate(UID)
    assert err.value.status == 503
    assert seller.status(UID)["available"] is False


def test_the_settings_form_cannot_flip_it(client, dbmod):
    """The switch is a reserved prefs key outside /api/prefs' whitelist, so
    the one way on is the route the consent dialog calls."""
    client.post("/api/prefs", json={"bg_removal": {"active": 1},
                                    "allow_offers": 1})
    assert "bg_removal" not in dbmod.get_prefs(UID)


def test_the_routes_switch_it_on_and_off(client):
    assert client.get("/api/bg-removal/status").json()["active"] is False
    assert client.post("/api/bg-removal/activate").json()["active"] is True
    assert client.get("/api/bg-removal/status").json()["active"] is True
    assert client.post("/api/bg-removal/deactivate").json()["active"] is False


def test_an_anonymous_caller_is_told_to_log_in(client, monkeypatch):
    monkeypatch.setattr(client.main.deps, "uid", lambda request: None)
    assert client.get("/api/bg-removal/status").json()["active"] is False
    assert client.post("/api/bg-removal/activate").status_code == 401


# --- every route that cuts asks first ------------------------------------------

def test_an_upload_asking_for_cutouts_is_refused_while_it_is_off(client):
    files = [("files", ("a.jpg", _jpeg(), "image/jpeg"))]
    res = client.post("/api/upload", files=files, data={"remove_bg": "true"})
    assert res.status_code == 409
    assert "Settings" in res.json()["detail"]
    assert client.charged == [], "a refused cutout was charged for"


def test_add_photos_asking_for_cutouts_is_refused_while_it_is_off(
        client, monkeypatch):
    monkeypatch.setattr(client.main.deps, "assert_session_owner",
                        lambda *a, **k: None)
    files = [("files", ("a.jpg", _jpeg(), "image/jpeg"))]
    res = client.post("/api/upload-more/s1", files=files,
                      data={"remove_bg": "true"})
    assert res.status_code == 409
    assert client.charged == []


def test_a_bulk_pile_asking_for_cutouts_is_refused_while_it_is_off(
        client, monkeypatch):
    monkeypatch.setattr(client.main.config, "vision_ready", lambda: True)
    files = [("files", ("a.jpg", _jpeg(), "image/jpeg"))]
    res = client.post("/api/bulk/upload", files=files,
                      data={"remove_bg": "true"})
    assert res.status_code == 409


def test_the_studio_is_refused_while_it_is_off(client):
    res = client.post("/api/image/remove-bg",
                      files={"file": ("a.jpg", _jpeg(), "image/jpeg")})
    assert res.status_code == 409
    assert client.charged == []


def test_an_upload_without_cutouts_never_asks(client):
    """Off is not a gate on uploading: a pile sent as shot goes through."""
    files = [("files", ("a.jpg", _jpeg(), "image/jpeg"))]
    res = client.post("/api/upload", files=files, data={"remove_bg": "false"})
    assert res.status_code == 200, res.text


# --- the month's count ----------------------------------------------------------

def test_the_count_goes_up_for_what_was_cut_and_back_for_what_was_not(
        client, seller, monkeypatch):
    seller.activate(UID)

    def _optimize_all(src, dst, remove_bg=False, **kw):
        dst.mkdir(parents=True, exist_ok=True)
        out = []
        for i, path in enumerate(sorted(src.iterdir())):
            target = dst / f"img_{i:03d}.jpg"
            target.write_bytes(path.read_bytes())
            out.append({"file": target.name, "background_removed": i == 0,
                        **({} if i == 0 else {"bg_error": "found no item"})})
        return out

    monkeypatch.setattr(client.main.images, "optimize_all", _optimize_all)
    monkeypatch.setattr(client.main.deps, "in_background", lambda *a, **k: None)
    files = [("files", (f"{i}.jpg", _jpeg(), "image/jpeg")) for i in range(3)]

    res = client.post("/api/upload", files=files, data={"remove_bg": "true"})

    assert res.status_code == 200, res.text
    assert client.charged == [("image_ai", 3)]
    assert seller.status(UID)["used"] == 1, "two photos kept their backgrounds"


def test_a_pile_that_would_go_past_the_limit_is_refused_and_not_counted(
        client, seller, dbmod):
    seller.activate(UID)
    seller.reserve(UID, 9)
    files = [("files", (f"{i}.jpg", _jpeg(), "image/jpeg")) for i in range(2)]

    res = client.post("/api/upload", files=files, data={"remove_bg": "true"})

    assert res.status_code == 429
    assert "9 of your 10" in res.json()["detail"]
    assert client.charged == []
    assert seller.status(UID)["used"] == 9


def test_a_reservation_gives_back_no_more_than_it_took(seller):
    seller.activate(UID)
    held = seller.reserve(UID, 3)
    held.release(1)
    held.release(10)
    held.release()
    assert seller.status(UID)["used"] == 0


def test_a_limit_of_zero_means_no_cap(seller, monkeypatch):
    monkeypatch.setattr(config, "BG_REMOVAL_MONTHLY_LIMIT", 0)
    seller.activate(UID)
    seller.reserve(UID, 5000)
    assert seller.status(UID)["used"] == 5000


def test_a_counter_that_cannot_be_written_fails_open(seller, dbmod, monkeypatch):
    """The same policy as the token charge: refusing every cutout because a
    counter hiccuped costs more trust than a few uncounted photos."""
    seller.activate(UID)
    monkeypatch.setattr(dbmod, "bg_reserve", lambda *a, **k: None)
    held = seller.reserve(UID, 4)
    assert held.user_id is None
    held.release()  # and giving back nothing is not an error either


def test_deleting_the_account_deletes_its_count(seller, dbmod):
    seller.activate(UID)
    seller.reserve(UID, 2)
    assert dbmod.delete_user(UID) is not None
    assert dbmod.bg_usage(UID, seller.period_now()) == 0


# --- no database, no accounts ----------------------------------------------------

def test_without_a_database_the_key_is_the_switch(keyed, monkeypatch):
    from backend.services import bg_removal
    monkeypatch.setattr(bg_removal.db, "enabled", lambda: False)

    status = bg_removal.status(None)
    assert status["active"] is True and status["needs_activation"] is False
    held = bg_removal.reserve(None, 3)
    assert held.user_id is None


def test_without_a_database_and_without_a_key_nothing_is_cut(monkeypatch):
    from backend.services import bg_removal
    monkeypatch.setattr(config, "PHOTOROOM_API_KEY", "")
    monkeypatch.setattr(bg_removal.db, "enabled", lambda: False)
    assert bg_removal.status(None)["active"] is False
    with pytest.raises(bg_removal.Refused) as err:
        bg_removal.reserve(None, 1)
    assert err.value.status == 503

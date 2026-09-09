"""An EasyPost key is proved to work before it is stored, and never shown back.

A mistyped key stored as "connected" fails at the first label, in the
shipping dialog, where the seller cannot fix it. So the connect route makes
one EasyPost read first and refuses here. The status route answers 200
{connected: false} to a visitor -- the shell asks at boot, before login --
and to a seller only ever the last four characters of what they pasted.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from backend import main, ratelimit
from backend.services import easypost

KEY = "EZTK" + "k" * 30


@pytest.fixture()
def saved(monkeypatch):
    """A signed-in seller, a recording save, and no EasyPost."""
    ratelimit._hits.clear()
    monkeypatch.setattr(main.auth, "current_user", lambda request: {"id": "u1"})
    box = {}

    def _save(uid, marketplace, **fields):
        box.update(uid=uid, marketplace=marketplace, **fields)
        return True
    monkeypatch.setattr(main.db, "save_marketplace_account", _save)
    monkeypatch.setattr(main.db, "get_marketplace_account", lambda uid, m: (
        {"refresh_token": box.get("refresh_token", ""),
         "settings": box.get("settings", {})} if box else None))

    def _boom(*_a, **_k):
        raise AssertionError("EasyPost was asked before the key was checked")
    monkeypatch.setattr(easypost.httpx, "get", _boom)
    return box


@pytest.fixture()
def client():
    return TestClient(main.app)


def test_a_blank_key_is_refused_without_asking_anyone(saved, client):
    res = client.post("/api/easypost/connect", json={"api_key": "  "})
    assert res.status_code == 400
    assert saved == {}


def test_a_key_of_the_wrong_shape_is_refused_without_asking_anyone(saved, client):
    res = client.post("/api/easypost/connect", json={"api_key": "hunter2"})
    assert res.status_code == 400
    assert "EZTK" in res.json()["detail"]
    assert saved == {}


def test_a_key_easypost_rejects_is_not_saved(saved, client, monkeypatch):
    def _reject(key):
        raise easypost.EasyPostError(
            "EasyPost rejected the API key — reconnect it in Settings.")
    monkeypatch.setattr(main.easypost, "verify_key", _reject)
    res = client.post("/api/easypost/connect", json={"api_key": KEY})
    assert res.status_code == 400
    assert saved == {}


def test_easypost_being_down_is_not_a_bad_key(saved, client, monkeypatch):
    def _down(key):
        raise easypost.EasyPostError("Couldn't reach EasyPost: timed out")
    monkeypatch.setattr(main.easypost, "verify_key", _down)
    res = client.post("/api/easypost/connect", json={"api_key": KEY})
    assert res.status_code == 502
    assert saved == {}


def test_a_working_key_is_saved_with_its_mode(saved, client, monkeypatch):
    monkeypatch.setattr(main.easypost, "verify_key",
                        lambda key: {"test": True, "carriers": ["USPS"]})
    res = client.post("/api/easypost/connect", json={"api_key": KEY})
    assert res.status_code == 200
    assert saved["refresh_token"] == KEY
    assert saved["marketplace"] == "easypost"
    assert saved["settings"]["mode"] == "test"
    assert saved["settings"]["key_hint"] == "kkkk"
    body = res.json()
    assert body["connected"] is True and body["test"] is True
    assert KEY not in res.text


def test_a_save_that_did_not_land_is_not_reported_as_connected(saved, client, monkeypatch):
    monkeypatch.setattr(main.easypost, "verify_key",
                        lambda key: {"test": False, "carriers": []})
    monkeypatch.setattr(main.db, "save_marketplace_account",
                        lambda uid, m, **f: False)
    res = client.post("/api/easypost/connect", json={"api_key": KEY})
    assert res.status_code == 503


def test_status_never_carries_the_key(saved, client, monkeypatch):
    monkeypatch.setattr(main.easypost, "verify_key",
                        lambda key: {"test": True, "carriers": ["USPS"]})
    client.post("/api/easypost/connect", json={"api_key": KEY})
    body = client.get("/api/easypost/status").json()
    assert body == {"connected": True, "test": True, "key_hint": "kkkk",
                    "carriers": ["USPS"]}


def test_a_visitor_sees_not_connected_rather_than_a_refusal(client, monkeypatch):
    monkeypatch.setattr(main.auth, "current_user", lambda request: None)
    res = client.get("/api/easypost/status")
    assert res.status_code == 200
    assert res.json() == {"connected": False}


def test_rates_without_a_key_point_at_settings(client, monkeypatch):
    monkeypatch.setattr(main.auth, "current_user", lambda request: {"id": "u1"})
    monkeypatch.setattr(main.db, "get_marketplace_account", lambda uid, m: None)
    res = client.post("/api/easypost/rates", json={"order_id": "1"})
    assert res.status_code == 400
    assert "Settings" in res.json()["detail"]


def test_disconnecting_forgets_the_key(saved, client, monkeypatch):
    dropped = {}
    monkeypatch.setattr(main.db, "disconnect_marketplace_account",
                        lambda uid, m: dropped.update(uid=uid, m=m))
    assert client.post("/api/easypost/disconnect", json={}).json() == {"ok": True}
    assert dropped == {"uid": "u1", "m": "easypost"}

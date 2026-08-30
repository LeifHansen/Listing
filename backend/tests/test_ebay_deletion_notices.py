"""eBay account-deletion notices must be verified, recorded, and acted on.

The endpoint used to parse the body for a log line and return 200 to anything
at all: no signature check, no use of the userId it carries, and no deletion.
Three separate failures in one handler —

  - compliance: eBay requires an app that stores eBay data to process these;
  - identity: EbayAccount held only the seller's MUTABLE username, so a notice
    could not be resolved to the data it asks us to erase even in principle;
  - durability: a 200 retires the notice (eBay stops resending), so answering
    before recording anything is a promise with nothing behind it.

The order the handler now uses is the design, and each step below is a way it
could go wrong. Verification in particular has to land WITH the deletion, never
after: an unauthenticated public URL that erases accounts on request is worse
than one that does nothing.

Contract: https://developer.ebay.com/develop/guides/sell/marketplace-user-account-deletion
"""
from __future__ import annotations

import json

import pytest

# Importing backend.main pulls the whole app in. The `checks` job installs
# neither of these, so it skips this file; the smoke job's "API tests" step is
# where it runs, and that step fails on a skip so this can never quietly stop
# running.
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

NOTIF_ID = "n-0001"
EBAY_USER_ID = "ebay-immutable-42"


def _notice(notification_id: str = NOTIF_ID,
            user_id: str = EBAY_USER_ID) -> bytes:
    return json.dumps({
        "metadata": {"topic": "MARKETPLACE_ACCOUNT_DELETION"},
        "notification": {
            "notificationId": notification_id,
            "eventDate": "2026-08-29T00:00:00.000Z",
            "data": {"username": "some-seller", "userId": user_id,
                     "eiasToken": "legacy"},
        },
    }).encode("utf-8")


@pytest.fixture()
def signed(monkeypatch):
    """A real ECDSA keypair, so the signature path is genuinely exercised."""
    from backend.services import ebay_notify

    key = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(ebay_notify, "public_key_for",
                        lambda _kid: key.public_key())

    def sign(raw: bytes) -> str:
        import base64
        from cryptography.hazmat.primitives import hashes

        sig = key.sign(raw, ec.ECDSA(hashes.SHA1()))
        header = {"alg": "ecdsa", "kid": "k1", "digest": "SHA1",
                  "signature": base64.b64encode(sig).decode()}
        return base64.b64encode(json.dumps(header).encode()).decode()

    return sign


@pytest.fixture()
def client(monkeypatch):
    from backend import db, main

    recorded, finished, purged = {}, [], []

    def _record(nid, uid, digest):
        if nid in recorded:
            return "duplicate"
        recorded[nid] = {"ebay_user_id": uid, "digest": digest}
        return "new"

    monkeypatch.setattr(db, "record_deletion_notice", _record)
    monkeypatch.setattr(db, "finish_deletion_notice",
                        lambda nid, state, err="": finished.append((nid, state)))
    monkeypatch.setattr(db, "find_users_by_ebay_user_id",
                        lambda eid: ["our-user-1"] if eid == EBAY_USER_ID else [])
    monkeypatch.setattr(db, "delete_user", lambda uid: ["listing-a", "listing-b"])
    monkeypatch.setattr(main, "_purge_session_images", purged.append)
    # Run the background erase inline so the test can assert on it.
    monkeypatch.setattr(main, "_in_background",
                        lambda fn, *a, what="", **k: fn(*a, **k))

    return TestClient(main.app), recorded, finished, purged


def _post(client, body: bytes, signature: str = ""):
    return client.post("/api/ebay/account-deletion", content=body,
                       headers={"x-ebay-signature": signature,
                                "content-type": "application/json"})


# --------------------------------------------------------- verification

def test_an_unsigned_notice_is_refused(client):
    """The old handler returned 200 to this. Anyone who knows the URL can
    post one."""
    api, recorded, _, purged = client
    assert _post(api, _notice()).status_code == 412
    assert recorded == {} and purged == []


def test_a_forged_signature_is_refused(client):
    api, recorded, _, purged = client
    assert _post(api, _notice(), "not-even-base64").status_code == 412
    assert recorded == {} and purged == []


def test_a_signature_for_a_different_body_is_refused(client, signed):
    """The signature must cover THESE bytes. A valid signature lifted from
    another notice must not authorise this one."""
    api, recorded, _, purged = client
    stolen = signed(_notice(notification_id="somebody-elses"))
    assert _post(api, _notice(), stolen).status_code == 412
    assert recorded == {} and purged == []


def test_a_tampered_body_is_refused(client, signed):
    """Sign a real notice, then change whose account it names."""
    api, recorded, _, purged = client
    body = _notice()
    signature = signed(body)
    # Same length, so the ONLY difference is whose account is named.
    other = b"somebody-elses-id"
    assert len(other) == len(EBAY_USER_ID)
    tampered = body.replace(EBAY_USER_ID.encode(), other)
    assert tampered != body
    assert _post(api, tampered, signature).status_code == 412
    assert recorded == {} and purged == []


def test_a_valid_notice_is_accepted(client, signed):
    api, recorded, _, _ = client
    body = _notice()
    assert _post(api, body, signed(body)).status_code == 200
    assert recorded[NOTIF_ID]["ebay_user_id"] == EBAY_USER_ID


# ------------------------------------------------------------- erasure

def test_a_valid_notice_erases_the_matched_account(client, signed):
    """The point of the whole endpoint, and the part that did not exist."""
    api, _, finished, purged = client
    body = _notice()
    _post(api, body, signed(body))

    assert purged == ["listing-a", "listing-b"]
    assert finished == [(NOTIF_ID, "done")]


def test_an_unknown_account_is_recorded_as_no_match_not_failure(client, signed):
    """eBay notifies about sellers who never connected here. That is a
    legitimate outcome, and must be distinguishable from a broken purge."""
    api, _, finished, purged = client
    body = _notice(user_id="never-connected")
    assert _post(api, body, signed(body)).status_code == 200
    assert purged == []
    assert finished == [(NOTIF_ID, "no_match")]


def test_a_redelivery_does_not_erase_twice(client, signed):
    """eBay resends until it gets a 2xx, so the same notice arrives more than
    once as a matter of routine."""
    api, _, finished, purged = client
    body = _notice()
    signature = signed(body)
    assert _post(api, body, signature).status_code == 200
    assert _post(api, body, signature).status_code == 200
    assert purged == ["listing-a", "listing-b"]
    assert len(finished) == 1


# ----------------------------------------------------------- durability

def test_a_notice_that_cannot_be_recorded_is_not_acknowledged(client, signed,
                                                              monkeypatch):
    """The sharpest one. A 200 retires the notice — eBay stops resending — so
    answering before the record commits loses the request entirely, with
    nothing anywhere showing that anyone ever asked."""
    from backend import db

    api, _, _, purged = client

    def _boom(*_a, **_k):
        raise db.StorageUnavailable("database is down")

    monkeypatch.setattr(db, "record_deletion_notice", _boom)
    body = _notice()
    assert _post(api, body, signed(body)).status_code == 503
    assert purged == []


def test_a_lookup_failure_is_failed_not_no_match(client, signed, monkeypatch):
    """"We could not ask" must never be recorded as "nobody matched" — that
    would retire a real erasure as complete."""
    from backend import db

    api, _, finished, purged = client
    monkeypatch.setattr(db, "find_users_by_ebay_user_id",
                        lambda _eid: db.UNAVAILABLE)
    body = _notice()
    assert _post(api, body, signed(body)).status_code == 200
    assert purged == []
    assert finished == [(NOTIF_ID, "failed")]


def test_a_signed_notice_missing_its_subject_is_refused(client, signed):
    """Signed by eBay but carrying nothing we can act on. Acknowledging would
    retire a notice that can never be resolved."""
    api, recorded, _, _ = client
    body = json.dumps({"notification": {"notificationId": "n-2", "data": {}}}
                      ).encode()
    assert _post(api, body, signed(body)).status_code == 400
    assert recorded == {}


def test_the_payload_itself_is_not_retained(client, signed):
    """The notice is personal data about someone asking to be forgotten.
    Keeping it to prove we deleted them would be its own violation, so only a
    digest is stored."""
    api, recorded, _, _ = client
    body = _notice()
    _post(api, body, signed(body))
    stored = json.dumps(recorded[NOTIF_ID])
    assert "some-seller" not in stored
    assert len(recorded[NOTIF_ID]["digest"]) == 64


# ------------------------------------------------------------ identity

def test_the_immutable_user_id_is_persisted_on_connect():
    """Without it a notice cannot be resolved to anything, because the only
    identifier it carries is the one this app used to throw away."""
    from backend import db, ebay_auth

    ident = ebay_auth.identity_display(
        {"userId": "u-immutable", "username": "renameable",
         "individualAccount": {"email": "s@example.com"}})
    assert ident["user_id"] == "u-immutable"
    assert "ebay_user_id" in db._EBAY_FIELDS


def test_the_subject_is_read_from_user_id_never_the_username():
    """A username is mutable: matching on it misses a seller who renamed, and
    a reused handle could erase the WRONG account."""
    from backend.services import ebay_deletion

    payload = json.loads(_notice())
    assert ebay_deletion.subject_of(payload) == EBAY_USER_ID

    payload["notification"]["data"].pop("userId")
    assert ebay_deletion.subject_of(payload) == ""

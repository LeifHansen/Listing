"""The third marketplace client, and the same question.

`_request` is Depop's single choke point and it already knows the METHOD, so
"could this have changed something?" is answerable without a table of call
names: POST/PUT/PATCH/DELETE can, GET cannot.

It answered every ending the same way. A read timeout or a 5xx on
create_product came back as "Depop rejected the listing" -- the title the fix
panel and the bulk cards render -- so a seller whose answer went missing was
told Depop had refused, edited a field, and published a second product.
"""
from __future__ import annotations

import httpx
import pytest

from backend.services import depop


class _Resp:
    def __init__(self, status=200, text="", payload=None, content=b"{}"):
        self.status_code = status
        self.text = text
        self.content = content
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


@pytest.fixture()
def transport(monkeypatch):
    def _serve(outcome):
        def _go(*_a, **_k):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        monkeypatch.setattr(depop.httpx, "request", _go)
    return _serve


def _create(transport, outcome):
    transport(outcome)
    with pytest.raises(depop.DepopError) as caught:
        depop.create_product("tok", {"title": "A thing"})
    return caught.value


@pytest.mark.parametrize("failure", [
    httpx.ReadTimeout("timed out"),
    httpx.ReadError("connection reset"),
    httpx.RemoteProtocolError("server disconnected"),
])
def test_a_lost_answer_to_a_create_is_unknown(transport, failure):
    assert isinstance(_create(transport, failure), depop.UnknownOutcome)


def test_a_server_error_on_a_create_is_unknown(transport):
    assert isinstance(_create(transport, _Resp(status=500, text="boom")),
                      depop.UnknownOutcome)


@pytest.mark.parametrize("failure", [
    httpx.ConnectTimeout("timed out connecting"),
    httpx.ConnectError("connection refused"),
])
def test_a_create_that_never_left_is_a_plain_failure(transport, failure):
    assert not isinstance(_create(transport, failure), depop.UnknownOutcome)


def test_depops_own_rejection_stays_definitive(transport):
    err = _create(transport, _Resp(status=400,
                                   payload={"message": "Title is required."}))
    assert not isinstance(err, depop.UnknownOutcome)
    assert err.issues[0]["title"] == "Depop rejected the listing"
    assert "Title is required." in err.issues[0]["fix"]


def test_the_headline_does_not_claim_a_rejection(transport):
    issue = _create(transport, httpx.ReadTimeout("timed out")).issues[0]
    assert "rejected" not in issue["title"].lower()
    assert "check" in (issue["title"] + " " + issue["fix"]).lower()


def test_a_lost_read_is_not_an_unknown_outcome(transport):
    """Nothing on Depop moved, so there is no outcome to be in doubt about."""
    transport(httpx.ReadTimeout("timed out"))
    with pytest.raises(depop.DepopError) as caught:
        depop._request("GET", "/v1/products/1", "tok")
    assert not isinstance(caught.value, depop.UnknownOutcome)


def test_a_delete_is_a_write_too(transport):
    """Ending a product is as unrepeatable as creating one — and a DELETE
    whose answer was lost is exactly the case where a retry reports 'no such
    product' and the seller concludes it never worked."""
    transport(httpx.ReadTimeout("timed out"))
    with pytest.raises(depop.DepopError) as caught:
        depop.delete_product("tok", "p-1")
    assert isinstance(caught.value, depop.UnknownOutcome)

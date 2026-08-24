"""eBay error 240 must be reported as what it is, with what eBay actually said.

    "The item cannot be listed or modified. The title and/or description may
     contain improper words, or the listing or seller may be in violation of
     eBay policy."

That single sentence names four unrelated causes, and eBay's own guidance is
that the real one arrives in the response's <Message> element. This client used
to drop that element and hand the sentence to ebay_errors, where the word
"title" won the branch race — so a seller whose ACCOUNT was blocked got told to
shorten a title that was never the problem, on every listing, forever.
"""
from __future__ import annotations

import pytest

from backend import ebay_errors
from backend.services import ebay_account, ebay_trading

NS = "urn:ebay:apis:eBLBaseComponents"

E240 = ("The item cannot be listed or modified. The title and/or description "
        "may contain improper words, or the listing or seller may be in "
        "violation of eBay policy.")


def _response(*, errors, message="", ack="Failure") -> bytes:
    blocks = "".join(
        f"<Errors><SeverityCode>Error</SeverityCode>"
        f"<ShortMessage>{short}</ShortMessage>"
        f"<LongMessage>{long}</LongMessage>"
        f"<ErrorCode>{code}</ErrorCode></Errors>"
        for short, long, code in errors)
    msg = f"<Message>{message}</Message>" if message else ""
    return (f'<?xml version="1.0" encoding="utf-8"?>'
            f'<AddFixedPriceItemResponse xmlns="{NS}">'
            f"<Ack>{ack}</Ack>{msg}{blocks}"
            f"</AddFixedPriceItemResponse>").encode()


class _Resp:
    status_code = 200

    def __init__(self, content):
        self.content = content


@pytest.fixture
def call(monkeypatch):
    """Run _call against a canned eBay response body."""
    def run(body):
        monkeypatch.setattr(ebay_trading.httpx, "post",
                            lambda *a, **k: _Resp(body))
        return ebay_trading._call("AddFixedPriceItem", "tok", "<Item/>")
    return run


# --- the client keeps everything eBay said ---------------------------------

def test_the_response_message_becomes_the_reason(call):
    """eBay puts the actual cause here — it is the whole point of error 240."""
    detail = ("Your account is not currently able to list items. Please "
              "contact Customer Support.")
    with pytest.raises(ebay_trading.TradingError) as exc:
        call(_response(errors=[("Cannot list", E240, "240")], message=detail))
    assert exc.value.code == "240"
    assert detail in exc.value.detail
    assert detail in str(exc.value), "the seller must see the real reason"


def test_a_240_with_no_message_still_reports_ebays_sentence(call):
    with pytest.raises(ebay_trading.TradingError) as exc:
        call(_response(errors=[("Cannot list", E240, "240")]))
    assert exc.value.code == "240"
    assert "cannot be listed or modified" in str(exc.value)


def test_errors_after_the_first_are_not_thrown_away(call):
    with pytest.raises(ebay_trading.TradingError) as exc:
        call(_response(errors=[("A", "The category is invalid.", "10007"),
                               ("B", "The price is invalid.", "10008")]))
    assert "The price is invalid." in exc.value.detail


def test_auth_codes_still_ask_for_a_reconnect(call):
    with pytest.raises(ebay_trading.TradingError) as exc:
        call(_response(errors=[("Auth", "Invalid token.", "931")]))
    assert "reconnect ebay" in str(exc.value).lower()


def test_a_successful_call_is_untouched(call):
    root = call(_response(errors=[], ack="Success"))
    assert ebay_trading._text(root, "Ack") == "Success"


# --- the explanation the seller reads --------------------------------------

def test_240_is_not_reported_as_a_title_problem():
    issue = ebay_errors.explain({"errorId": "240", "message": E240})
    assert issue["target"] != "title"
    assert "80 characters" not in issue["fix"]


def test_240_points_at_the_account():
    issue = ebay_errors.explain({"errorId": "240", "message": E240})
    assert issue["target"] == "account"
    assert "account" in issue["title"].lower()


def test_240_is_recognised_from_the_wording_alone():
    """The message reaches ebay_errors as plain text on some paths, with no
    error id attached."""
    issues = ebay_errors.from_response(E240)
    assert issues[0]["target"] == "account"


def test_from_trading_error_carries_ebays_detail():
    exc = ebay_trading.TradingError(E240, code="240",
                                    detail="Your account is restricted.")
    issue = ebay_errors.from_trading_error(exc)[0]
    assert issue["target"] == "account"
    assert "restricted" in issue["ebay_detail"]


# --- the substring bug next door -------------------------------------------

def test_clean_and_means_do_not_look_like_an_ean():
    """"ean" as a bare substring matched "clean" and "means", filing unrelated
    rejections under "eBay wants a product identifier"."""
    issue = ebay_errors.explain(
        {"errorId": "25002", "message": "The photo is not clean enough."})
    assert issue["target"] != "specifics"


def test_a_real_ean_still_lands_on_item_specifics():
    issue = ebay_errors.explain(
        {"errorId": "25002", "message": "An EAN is required for this item."})
    assert issue["target"] == "specifics"


# --- the diagnosis attached to a 240 ---------------------------------------

def test_a_240_asks_ebay_whether_payments_is_the_hold():
    """240 repeats on every listing and names no cause. Payments onboarding is
    the most common one and the only one the API states plainly, so the failure
    path spends one call to find out."""
    issues = ebay_account.publish_block_issues(
        ebay_trading.TradingError(E240, code="240"), {"access_token": "tok"},
        payments=lambda _t: {"status": "NOT_OPTED_IN"})
    assert any("payments setup" in i["title"] for i in issues)


def test_an_opted_in_account_gets_no_payments_claim():
    issues = ebay_account.publish_block_issues(
        ebay_trading.TradingError(E240, code="240"), {"access_token": "tok"},
        payments=lambda _t: {"status": "OPTED_IN"})
    assert not any("payments setup" in i["title"] for i in issues)


def test_the_diagnosis_never_replaces_the_rejection():
    """A failing side-check must still leave the seller with eBay's reason."""
    def boom(_t):
        raise RuntimeError("eBay is down")

    issues = ebay_account.publish_block_issues(
        ebay_trading.TradingError(E240, code="240"), {"access_token": "tok"},
        payments=boom)
    assert issues and issues[0]["target"] == "account"


def test_other_rejections_cost_no_extra_call():
    def boom(_t):
        raise AssertionError("must not be called")

    issues = ebay_account.publish_block_issues(
        ebay_trading.TradingError("The price is invalid.", code="10008"),
        {"access_token": "tok"}, payments=boom)
    assert issues[0]["target"] == "price"


def test_ebays_own_reason_wins_when_it_gave_one():
    """A 240 that comes with a real <Message> should read as that message, not
    as the generic account explanation."""
    exc = ebay_trading.TradingError(
        E240, code="240",
        detail="The word “authentic” requires proof of authenticity.")
    issue = ebay_errors.from_trading_error(exc)[0]
    assert "authenticity" in issue["fix"]
    assert "Customer Service" not in issue["fix"]

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
    assert "account" in issue["fix"].lower()


def test_an_unexplained_240_is_marked_as_a_placeholder():
    """It reports that a publish stopped and names no cause — so it must not
    outrank a diagnosis in the surfaces that show a single line."""
    issue = ebay_errors.explain({"errorId": "240", "message": E240})
    assert issue["placeholder"] is True


def test_a_240_ebay_explained_is_not_a_placeholder():
    issue = ebay_errors.explain(
        {"errorId": "240", "message": E240,
         "longMessage": "Your account is restricted from listing."})
    assert issue.get("placeholder") is False
    assert "restricted" in issue["fix"]


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
        said="The word “authentic” requires proof of authenticity.")
    issue = ebay_errors.from_trading_error(exc)[0]
    assert "authenticity" in issue["fix"]
    assert "Customer Service" not in issue["fix"]


def test_ebays_reason_is_in_the_title_not_only_the_fix():
    """Every one-line surface renders the title alone. A reason that lives only
    in `fix` is a reason the seller never reads — which is how "eBay won't
    accept this listing" managed to say less than the placeholder it replaced.
    """
    exc = ebay_trading.TradingError(
        E240, code="240",
        said="The word “authentic” requires proof of authenticity.")
    issue = ebay_errors.from_trading_error(exc)[0]
    assert "authentic" in issue["title"]
    assert issue["title"] != "eBay won't accept this listing"


def test_a_warning_is_never_quoted_as_ebays_reason():
    """`detail` carries warnings and trailing errors as well as <Message>, so
    it cannot speak for WHY a listing was refused. Reading it as eBay's reason
    made the app quote a warning about something else as the cause — and, by
    counting the rejection as explained, pushed the real diagnosis (the probe's
    verdict) off the card behind it."""
    exc = ebay_trading.TradingError(
        E240, code="240",
        detail="Warning: the item was listed with a shorter handling time.")
    issue = ebay_errors.from_trading_error(exc)[0]
    assert "handling time" not in issue["title"]
    assert "handling time" not in issue["fix"]
    # Unexplained, so the diagnosis is free to lead instead of trailing it.
    assert issue["placeholder"] is True


# --- a 240 also asks about registration and selling limits ------------------
#
# eBay error 240 carries no field to fix, and its wording ("the listing or
# seller may be in violation of eBay policy") is four causes in a trench coat.
# The payments check named one of them. getPrivileges names two more, and it
# already existed — it just was not wired into this path, so a seller whose
# account was simply not finished being set up got the generic sentence and
# nowhere to go.

def _blocked():
    from backend.services.ebay_trading import TradingError
    return TradingError("eBay says no", code="240")


CREDS = {"access_token": "tok"}
OK_PAYMENTS = lambda _t: {"status": "OPTED_IN"}          # noqa: E731
NO_PRIV = lambda _t: None                                 # noqa: E731


def test_an_unfinished_registration_is_named(monkeypatch):
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, payments=OK_PAYMENTS,
        privileges=lambda _t: {"registration_complete": False,
                               "selling_limit": None})
    titles = [i["title"] for i in issues]
    assert any("finished setting this account up to sell" in t for t in titles)


def test_a_finished_registration_says_nothing(monkeypatch):
    """No news is not a finding. Inventing an account problem sends the seller
    to argue with eBay support over a message this app made up."""
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, payments=OK_PAYMENTS,
        privileges=lambda _t: {"registration_complete": True,
                               "selling_limit": None})
    assert not any("setting this account up" in i["title"] for i in issues)


def test_a_zero_selling_limit_is_named(monkeypatch):
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, payments=OK_PAYMENTS,
        privileges=lambda _t: {"registration_complete": True,
                               "selling_limit": {"quantity": 0, "amount": "0.0",
                                                 "currency": "USD"}})
    assert any("selling limit" in i["title"] for i in issues)


def test_a_healthy_limit_is_not_reported_as_exhausted():
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, payments=OK_PAYMENTS,
        privileges=lambda _t: {"registration_complete": True,
                               "selling_limit": {"quantity": 10,
                                                 "amount": "500.0",
                                                 "currency": "USD"}})
    assert not any("selling limit" in i["title"] for i in issues)


def test_an_unreadable_limit_is_never_called_exhausted():
    """eBay sends the amount as a string and omits the block entirely for
    uncapped accounts. A figure we cannot read is not a cap of nothing."""
    for limit in ({"quantity": None, "amount": None},
                  {"quantity": "many", "amount": "lots"},
                  {}):
        issues = ebay_account.publish_block_issues(
            _blocked(), CREDS, payments=OK_PAYMENTS,
            privileges=lambda _t, _l=limit: {"registration_complete": True,
                                             "selling_limit": _l})
        assert not any("selling limit" in i["title"] for i in issues), limit


def test_a_failed_payments_check_no_longer_skips_the_rest(monkeypatch):
    """It used to `return issues` when the payments lookup raised, so an eBay
    blip on THAT call meant the seller learned nothing about registration
    either — two independent diagnoses coupled by an early return."""
    def _boom(_t):
        raise RuntimeError("payments API down")
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, payments=_boom,
        privileges=lambda _t: {"registration_complete": False,
                               "selling_limit": None})
    assert any("finished setting this account up to sell" in i["title"]
               for i in issues)


def test_unreadable_privileges_add_nothing(monkeypatch):
    """Nothing was learned, so nothing may be claimed: the seller is left with
    eBay's own unexplained rejection and no invented cause beside it."""
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, payments=OK_PAYMENTS, privileges=NO_PRIV)
    assert [i.get("placeholder") for i in issues] == [True]


def test_the_original_rejection_always_survives():
    """Every diagnosis is additive. The rejection is what the seller needs."""
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, payments=OK_PAYMENTS,
        privileges=lambda _t: {"registration_complete": False,
                               "selling_limit": None})
    assert any(i.get("error_id") == "240" for i in issues)


def test_a_named_cause_leads_the_unexplained_rejection():
    """The bulk card, the drafts strip and the publish toast all show ONE
    line, and they show the first issue. With eBay's cause-less 240 first,
    every one of them said "eBay refused this listing and wouldn't say why"
    while the sentence naming the actual hold sat underneath, unread — which
    is precisely what a seller staring at seven identical failures reported.
    """
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, payments=lambda _t: {"status": "NOT_OPTED_IN"},
        privileges=NO_PRIV)
    assert "payments setup" in issues[0]["title"]
    assert issues[-1]["placeholder"] is True


# --- the probe: is it the account, or is it this listing? -------------------
#
# The account APIs answer two of error 240's four causes. When they come up
# empty the seller is still where they started, so eBay is asked the one
# question that separates the rest: it re-checks the same listing with plain
# wording, through a Verify call that creates nothing.

class _Draft:
    """The parts of a Listing this probe touches."""

    def __init__(self, title="Royal Stafford Sweetpea Teacup", description="Lovely."):
        self.title = title
        self.description = description

    def model_copy(self, update):
        return _Draft(update.get("title", self.title),
                      update.get("description", self.description))


def _refusing(*, plain: bool, real: bool = True):
    """A verify() that refuses with a 240 — `real` for the listing's own
    wording, `plain` for the neutral rewrite.

    It accepts the verifier's real keyword arguments and ignores them, so a
    `plain=True` double models an account that refuses everything: the plain
    rewrite AND every payload variant the probe goes on to try. Without them
    it would model a verifier too old to answer those questions, which is a
    different verdict entirely.
    """
    def verify(candidate, *, with_policies: bool = True,
               with_photos: bool = True):
        blocked = plain if candidate.title == ebay_account.NEUTRAL_TITLE else real
        if blocked:
            raise ebay_trading.TradingError(E240, code="240")
    return verify


@pytest.fixture(autouse=True)
def _clean_probe_cache():
    ebay_account.forget_verified()
    yield
    ebay_account.forget_verified()


def test_a_plain_listing_refused_too_means_the_account_is_held():
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_Draft(), verify=_refusing(plain=True),
        payments=OK_PAYMENTS, privileges=NO_PRIV)
    assert "refusing every listing from this account" in issues[0]["title"]
    assert issues[0]["target"] == "account"


def test_a_plain_listing_accepted_means_the_title_is_the_cause():
    """eBay took the same listing with a plain title and refused it with this
    one. That is the answer error 240 refuses to give — and it is the opposite
    of the advice the app hands out when it assumes an account hold."""
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_Draft(), verify=_refusing(plain=False),
        payments=OK_PAYMENTS, privileges=NO_PRIV)
    assert issues[0]["target"] == "title"
    assert "title" in issues[0]["title"]


def test_a_listing_accepted_with_its_own_title_points_at_the_description():
    def verify(candidate):
        if candidate.description != ebay_account.NEUTRAL_DESCRIPTION:
            raise ebay_trading.TradingError(E240, code="240")
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_Draft(), verify=verify,
        payments=OK_PAYMENTS, privileges=NO_PRIV)
    assert issues[0]["target"] == "description"


def test_an_inconclusive_probe_claims_nothing():
    """eBay answered with something else entirely (a validation error, an
    outage, a throttle). A probe that did not settle the question must leave
    the seller with the rejection alone rather than a guess."""
    def verify(_candidate):
        raise ebay_trading.TradingError("Category is invalid.", code="10007")
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_Draft(), verify=verify,
        payments=OK_PAYMENTS, privileges=NO_PRIV)
    assert [i.get("placeholder") for i in issues] == [True]


def test_the_probe_is_skipped_once_a_cause_is_already_named():
    """A diagnosis in hand is worth more than a dry run, and the dry run is
    two more calls against eBay on a path that is already failing."""
    def verify(_candidate):
        raise AssertionError("must not be called")
    ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_Draft(), verify=verify,
        payments=lambda _t: {"status": "NOT_OPTED_IN"}, privileges=NO_PRIV)


def test_no_listing_or_no_verifier_means_no_probe():
    for kwargs in ({"listing": None, "verify": _refusing(plain=True)},
                   {"listing": _Draft(), "verify": None}):
        issues = ebay_account.publish_block_issues(
            _blocked(), CREDS, payments=OK_PAYMENTS, privileges=NO_PRIV,
            **kwargs)
        assert [i.get("placeholder") for i in issues] == [True]


def test_an_account_verdict_is_reused_across_a_bulk_run():
    """Seven drafts failing together is the case that matters: the account is
    either held or it isn't, and asking eBay seven times is six wasted round
    trips on a path that is already failing."""
    calls = []
    inner = _refusing(plain=True)

    def verify(candidate, **kwargs):
        calls.append(candidate.title)
        inner(candidate, **kwargs)

    creds = dict(CREDS, _uid="u1")
    for _ in range(7):
        issues = ebay_account.publish_block_issues(
            _blocked(), creds, listing=_Draft(), verify=verify,
            payments=OK_PAYMENTS, privileges=NO_PRIV)
        assert "refusing every listing" in issues[0]["title"]
    # The first draft pays for the walk: the plain rewrite plus one probe per
    # payload dimension. The other six reuse its verdict and ask nothing.
    assert len(calls) == 1 + len(ebay_account._PAYLOAD_PROBES)


def test_a_wording_verdict_is_never_reused():
    """It is true of ONE listing. Cached, it would tell the next listing its
    title is the problem without ever asking eBay about that title."""
    calls = []

    def verify(candidate):
        calls.append(candidate.title)
        if candidate.title != ebay_account.NEUTRAL_TITLE:
            raise ebay_trading.TradingError(E240, code="240")

    creds = dict(CREDS, _uid="u2")
    for _ in range(2):
        ebay_account.publish_block_issues(
            _blocked(), creds, listing=_Draft(), verify=verify,
            payments=OK_PAYMENTS, privileges=NO_PRIV)
    assert len(calls) == 4  # two probes per publish, neither remembered


def test_a_disconnect_forgets_the_account_verdict():
    """The hold belonged to the account that was connected. Carried across a
    switch, it tells a healthy account it is blocked."""
    creds = dict(CREDS, _uid="u3")
    ebay_account.publish_block_issues(
        _blocked(), creds, listing=_Draft(), verify=_refusing(plain=True),
        payments=OK_PAYMENTS, privileges=NO_PRIV)
    ebay_account.forget_verified("u3")
    def verify(_candidate):
        raise AssertionError("cache should have been cleared") \
            if False else None
    issues = ebay_account.publish_block_issues(
        _blocked(), creds, listing=_Draft(), verify=verify,
        payments=OK_PAYMENTS, privileges=NO_PRIV)
    # eBay now accepts everything, so the stale "account held" verdict is gone.
    assert not any("refusing every listing" in i["title"] for i in issues)


# --- the dry run itself -----------------------------------------------------

def test_verify_uses_ebays_dry_run_call_and_creates_nothing(monkeypatch):
    """VerifyAddFixedPriceItem validates exactly as the real call does and
    lists nothing. Sending the real call name here would post the listing the
    probe exists to avoid posting."""
    sent = {}

    def fake_post(url, headers=None, content=None, **kw):
        sent["call"] = headers["X-EBAY-API-CALL-NAME"]
        sent["body"] = content.decode()
        return _Resp(_response(errors=[], ack="Success"))

    monkeypatch.setattr(ebay_trading.httpx, "post", fake_post)
    listing = _listing()
    ebay_trading.verify_listing("tok", listing, ["https://x/1.jpg"],
                                postal_code="97201")
    assert sent["call"] == "VerifyAddFixedPriceItem"
    # No idempotency key: a dry run mints nothing, so there is nothing to make
    # repeatable — and a key here could collide with the publish it diagnoses.
    assert "UUID" not in sent["body"]
    assert "InventoryTrackingNumber" not in sent["body"]


def test_a_verify_rejection_keeps_ebays_error_code(monkeypatch):
    """The probe reads exc.code to tell a 240 from anything else."""
    monkeypatch.setattr(
        ebay_trading.httpx, "post",
        lambda *a, **k: _Resp(_response(errors=[("Cannot list", E240, "240")])))
    with pytest.raises(ebay_trading.TradingError) as exc:
        ebay_trading.verify_listing("tok", _listing(), [], postal_code="97201")
    assert exc.value.code == "240"


def _listing():
    from backend.models import Listing
    return Listing(title="Royal Stafford Sweetpea Teacup", price=22.0,
                   category_id="20642", description="Lovely.")


def test_warnings_on_a_failed_call_are_kept(monkeypatch):
    """eBay attaches warnings to a rejection, and on a catch-all code they are
    sometimes the only place the cause is named. They were dropped here without
    even a log line."""
    body = (f'<?xml version="1.0" encoding="utf-8"?>'
            f'<AddFixedPriceItemResponse xmlns="{NS}"><Ack>Failure</Ack>'
            f"<Errors><SeverityCode>Error</SeverityCode>"
            f"<LongMessage>{E240}</LongMessage><ErrorCode>240</ErrorCode></Errors>"
            f"<Errors><SeverityCode>Warning</SeverityCode>"
            f"<LongMessage>The title contains a restricted brand name."
            f"</LongMessage><ErrorCode>21919301</ErrorCode></Errors>"
            f"</AddFixedPriceItemResponse>").encode()
    monkeypatch.setattr(ebay_trading.httpx, "post", lambda *a, **k: _Resp(body))
    with pytest.raises(ebay_trading.TradingError) as exc:
        ebay_trading._call("AddFixedPriceItem", "tok", "<Item/>")
    assert "restricted brand name" in exc.value.detail


def test_a_warning_never_becomes_the_rejection_headline(monkeypatch):
    """Kept as context, not promoted: only eBay's response-level <Message>
    speaks for why a listing was refused."""
    body = (f'<?xml version="1.0" encoding="utf-8"?>'
            f'<AddFixedPriceItemResponse xmlns="{NS}"><Ack>Failure</Ack>'
            f"<Errors><SeverityCode>Error</SeverityCode>"
            f"<LongMessage>{E240}</LongMessage><ErrorCode>240</ErrorCode></Errors>"
            f"<Errors><SeverityCode>Warning</SeverityCode>"
            f"<LongMessage>Your listing was assigned a different category."
            f"</LongMessage><ErrorCode>21919188</ErrorCode></Errors>"
            f"</AddFixedPriceItemResponse>").encode()
    monkeypatch.setattr(ebay_trading.httpx, "post", lambda *a, **k: _Resp(body))
    with pytest.raises(ebay_trading.TradingError) as exc:
        ebay_trading._call("AddFixedPriceItem", "tok", "<Item/>")
    assert "different category" not in str(exc.value)
    assert "cannot be listed or modified" in str(exc.value)


def test_a_remapped_category_is_followed(monkeypatch):
    """eBay retires categories and moves the listing itself. The id in our
    record drives every later revise, aspect lookup and condition list, so a
    remap we ignore points all of them at a category the listing left."""
    body = (f'<?xml version="1.0" encoding="utf-8"?>'
            f'<AddFixedPriceItemResponse xmlns="{NS}"><Ack>Warning</Ack>'
            f"<ItemID>110512345678</ItemID><CategoryID>20642</CategoryID>"
            f"<Errors><SeverityCode>Warning</SeverityCode>"
            f"<LongMessage>The category was mapped to a new one."
            f"</LongMessage><ErrorCode>21916620</ErrorCode></Errors>"
            f"</AddFixedPriceItemResponse>").encode()
    monkeypatch.setattr(ebay_trading.httpx, "post", lambda *a, **k: _Resp(body))
    listing = _listing()
    listing.category_id = "13961"  # what we asked for
    res = ebay_trading.create_listing("tok", listing, ["https://x/1.jpg"],
                                      postal_code="97201")
    assert res["listing_id"] == "110512345678"
    assert res["category_id"] == "20642"


def test_an_unchanged_category_is_not_reported_as_a_remap(monkeypatch):
    body = (f'<?xml version="1.0" encoding="utf-8"?>'
            f'<AddFixedPriceItemResponse xmlns="{NS}"><Ack>Success</Ack>'
            f"<ItemID>110512345678</ItemID><CategoryID>20642</CategoryID>"
            f"</AddFixedPriceItemResponse>").encode()
    monkeypatch.setattr(ebay_trading.httpx, "post", lambda *a, **k: _Resp(body))
    listing = _listing()
    listing.category_id = "20642"
    assert "category_id" not in ebay_trading.create_listing(
        "tok", listing, [], postal_code="97201")


# --- the production case, end to end ----------------------------------------
#
# Seven drafts, an account eBay's own APIs call healthy, and a 240 carrying no
# <Message>. This is the exact shape the live logs showed:
#
#   trading: AddFixedPriceItem rejected — code=240 ... detail=(none)
#   ebay: publish blocked by error 240; payments=OPTED_IN registered=True
#         limit={'amount': '50000', 'currency': 'USD', 'quantity': 5000}
#
# Every account question comes back clean, so the probe's verdict is the ONLY
# information the seller can act on — and it has to arrive first, because the
# card renders one line.

HEALTHY_PRIV = lambda _t: {                                    # noqa: E731
    "registration_complete": True,
    "selling_limit": {"amount": "50000", "currency": "USD", "quantity": 5000},
}


def test_the_live_failure_puts_the_verdict_on_the_card():
    """A healthy account + a cause-less 240 => the probe's verdict leads."""
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_Draft(), verify=_refusing(plain=True),
        payments=OK_PAYMENTS, privileges=HEALTHY_PRIV)
    assert "refusing every listing from this account" in issues[0]["title"]
    assert issues[-1]["placeholder"] is True


def test_a_warning_cannot_bury_the_verdict():
    """The regression that put a content-free headline on the card.

    `detail` carries warnings, and a warning is almost always present. Reading
    it as eBay's reason marked the rejection "explained", which (a) quoted the
    warning as the cause and (b) cleared the placeholder flag — so the verdict
    lost the ordering and the seller got "eBay won't accept this listing" with
    nothing whatsoever underneath it.
    """
    refused = ebay_trading.TradingError(
        E240, code="240",
        detail="Warning: the listing was submitted with a shorter handling time.")
    issues = ebay_account.publish_block_issues(
        refused, CREDS, listing=_Draft(), verify=_refusing(plain=True),
        payments=OK_PAYMENTS, privileges=HEALTHY_PRIV)
    assert "refusing every listing from this account" in issues[0]["title"]
    assert "handling time" not in issues[0]["title"]
    assert "handling time" not in issues[0]["fix"]


def test_a_wording_verdict_reaches_the_card_too():
    """The same path when eBay's objection is the words, not the account."""
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_Draft(), verify=_refusing(plain=False),
        payments=OK_PAYMENTS, privileges=HEALTHY_PRIV)
    assert "title" in issues[0]["title"].lower()
    assert issues[0]["target"] == "title"


# --- the payload probes -----------------------------------------------------
#
# "Not the words" was being reported as "the account", and a publish carries
# plenty that is neither: the business policy ids (which come from the ACCOUNT,
# not the draft, and so break every listing at once), the photo URLs, the item
# specifics, the condition note. Telling a seller their account is held when
# eBay would take the listing with one field changed sends them to argue with
# Customer Service over something they could have fixed in a minute.

class _FullDraft:
    """A draft with the fields the payload probes vary."""

    def __init__(self, title="Royal Stafford Sweetpea Teacup",
                 description="Lovely.", item_specifics=("Type", "Teacup"),
                 condition_description="Light crazing."):
        self.title = title
        self.description = description
        self.item_specifics = list(item_specifics)
        self.condition_description = condition_description

    def model_copy(self, update):
        return _FullDraft(
            update.get("title", self.title),
            update.get("description", self.description),
            update.get("item_specifics", self.item_specifics),
            update.get("condition_description", self.condition_description))


def _refusing_unless(*, ok_without=None, inconclusive_at=None):
    """A verifier that refuses everything with a 240 except the one variant
    named by `ok_without` — the shape of eBay accepting a listing the moment
    a single field is dropped."""
    def verify(candidate, *, with_policies=True, with_photos=True):
        state = {
            "policies": not with_policies,
            "photos": not with_photos,
            "specifics": not candidate.item_specifics,
            "condition": not candidate.condition_description,
        }
        for field, dropped in state.items():
            if dropped and field == inconclusive_at:
                raise ebay_trading.TradingError("Rate limited", code="21919144")
            if dropped and field == ok_without:
                return
        raise ebay_trading.TradingError(E240, code="240")
    return verify


def test_a_bad_business_policy_is_not_called_an_account_hold():
    """The regression this whole path exists to prevent: policy ids come from
    the account, so a stale one fails every listing identically — which reads
    exactly like a hold until you drop them and eBay says yes."""
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_FullDraft(),
        verify=_refusing_unless(ok_without="policies"),
        payments=OK_PAYMENTS, privileges=HEALTHY_PRIV)
    assert issues[0]["target"] == "policies"
    assert "business policies" in issues[0]["title"]
    assert "account" not in issues[0]["title"].lower()


def test_the_photos_can_be_the_cause():
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_FullDraft(),
        verify=_refusing_unless(ok_without="photos"),
        payments=OK_PAYMENTS, privileges=HEALTHY_PRIV)
    assert issues[0]["target"] == "photos"


def test_an_item_specific_can_be_the_cause():
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_FullDraft(),
        verify=_refusing_unless(ok_without="specifics"),
        payments=OK_PAYMENTS, privileges=HEALTHY_PRIV)
    assert issues[0]["target"] == "specifics"


def test_the_account_verdict_now_means_everything_was_tried():
    """Only when dropping each part changes nothing is the account fair."""
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_FullDraft(),
        verify=_refusing_unless(ok_without=None),
        payments=OK_PAYMENTS, privileges=HEALTHY_PRIV)
    assert "refusing every listing from this account" in issues[0]["title"]
    assert "no business policies" in issues[0]["fix"]


def test_an_unfinished_walk_does_not_claim_the_account():
    """eBay answering something else partway through leaves the rest unknown,
    and the verdict has to say so instead of upgrading to a hold."""
    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_FullDraft(),
        verify=_refusing_unless(ok_without=None, inconclusive_at="policies"),
        payments=OK_PAYMENTS, privileges=HEALTHY_PRIV)
    assert "not over its wording" in issues[0]["title"]
    assert "refusing every listing" not in issues[0]["title"]


def test_a_policy_verdict_is_reused_across_a_bulk_run():
    """The ids are the account's, so seven drafts share one answer."""
    calls = []

    def counting(candidate, *, with_policies=True, with_photos=True):
        calls.append(with_policies)
        if not with_policies:
            return
        raise ebay_trading.TradingError(E240, code="240")

    for _ in range(3):
        issues = ebay_account.publish_block_issues(
            _blocked(), {**CREDS, "_uid": "u1"}, listing=_FullDraft(),
            verify=counting, payments=OK_PAYMENTS, privileges=HEALTHY_PRIV)
        assert issues[0]["target"] == "policies"
    assert calls, "the first draft must actually ask eBay"
    assert len(calls) <= 3, "the answer must not be re-bought per draft"


def test_an_old_verifier_falls_back_instead_of_guessing():
    """A verifier that predates the payload probes can't answer them; that is
    unknown, not a hold."""
    def old_style(candidate):
        raise ebay_trading.TradingError(E240, code="240")

    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_FullDraft(), verify=old_style,
        payments=OK_PAYMENTS, privileges=HEALTHY_PRIV)
    assert "not over its wording" in issues[0]["title"]


def test_a_second_error_alongside_the_240_still_counts_as_one():
    """Dropping a field to ask about it makes eBay add a complaint about the
    hole that leaves — take the business policies away and it wants a shipping
    service — and eBay may put that one first. Reading only the first code
    then says "not a 240" about a response that contains one."""
    def verify(candidate, *, with_policies=True, with_photos=True):
        raise ebay_trading.TradingError(
            "You must specify a shipping service.", code="10007",
            codes=["10007", "240"])

    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_FullDraft(), verify=verify,
        payments=OK_PAYMENTS, privileges=HEALTHY_PRIV)
    # Every variant still refused with a 240 among the codes => the account.
    assert "refusing every listing from this account" in issues[0]["title"]


def test_one_unanswerable_probe_does_not_abandon_the_rest():
    """The walk used to stop at the first muddled answer, so a listing whose
    PHOTOS were the cause reported "we couldn't check" because the policies
    question ahead of it came back inconclusive."""
    def verify(candidate, *, with_policies=True, with_photos=True):
        if not with_policies:                    # unanswerable
            raise ebay_trading.TradingError("Try later", code="21919144")
        if not with_photos:                      # the real cause
            return
        raise ebay_trading.TradingError(E240, code="240")

    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_FullDraft(), verify=verify,
        payments=OK_PAYMENTS, privileges=HEALTHY_PRIV)
    assert issues[0]["target"] == "photos"


def test_an_unanswered_probe_still_blocks_the_account_verdict():
    """A gap anywhere means the account cannot be declared: "we tried
    everything" has to be true when the app says it."""
    def verify(candidate, *, with_policies=True, with_photos=True):
        if not with_policies:
            raise ebay_trading.TradingError("Try later", code="21919144")
        raise ebay_trading.TradingError(E240, code="240")

    issues = ebay_account.publish_block_issues(
        _blocked(), CREDS, listing=_FullDraft(), verify=verify,
        payments=OK_PAYMENTS, privileges=HEALTHY_PRIV)
    assert "not over its wording" in issues[0]["title"]

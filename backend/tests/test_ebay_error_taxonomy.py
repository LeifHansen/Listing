"""One taxonomy for eBay's errors, and the Trading path that must reach it.

Every eBay rejection a seller sees is explained by `ebay_errors.explain`, and
live publishes reach it through `from_trading_error`.

Two mis-assigned error codes survived because a test entered through a door
production doesn't use. `test_sync_quota.py` asserts the selling-limit
explanation for 21919188 — and passed — by asking `explain` directly. On the
Trading path the same code was in `_DUPLICATE_CODES`, so it never reached the
taxonomy at all: the seller was told their listing "was already submitted"
and that publishing again "could create a duplicate", when nothing had been
created and the real problem was their monthly selling limit.

A test that enters through a door production doesn't use can stay green while
the behaviour it describes is broken, which is why the duplicate check below
is asked of the Trading client itself.
"""
from __future__ import annotations

from backend import ebay_errors
from backend.services import ebay_trading

# Codes whose meaning the app acts on, with what a seller should end up being
# told. Sources: eBay's error reference and, for the two that were wrong, the
# published behaviour of other listing tools.
#
#   21919188  "this listing would cause you to exceed the amount you can list"
#             — the MONTHLY SELLING LIMIT. Not a duplicate. eBay's
#             duplicate-listing-policy code is 21919067, a different thing.
#   21917053  Expired IAF token. This one really IS auth, despite a fixture
#             elsewhere in the suite pairing it with a call-limit message.
#   21919144  Maximum call limit exceeded — the seller-level add/revise rate.


def test_the_selling_limit_is_not_a_duplicate_submission():
    """21919188 must not be treated as "you already sent this".

    Fails against the old code, where 21919188 was in _DUPLICATE_CODES and
    this returned True — sending the seller a duplicate warning for a limit
    they need eBay to raise.
    """
    exc = ebay_trading.TradingError(
        "This listing would cause you to exceed the amount you can list.",
        code="21919188", detail="")
    assert not ebay_trading._is_duplicate_rejection(exc)


def test_the_selling_limit_is_not_read_as_a_price_problem():
    """The wording contains "amount", which the price branch matches on.

    Fails against a fix that only removes the code from _DUPLICATE_CODES
    without giving the taxonomy a code-keyed branch: the message reaches
    `explain`, the substring "amount" wins, and the seller is sent to fix a
    price that was never wrong.
    """
    issue = ebay_errors.explain({
        "errorId": 21919188,
        "message": "This listing would cause you to exceed the amount you "
                   "can list. Please contact eBay to raise your limit.",
    })
    assert issue["target"] != "price"
    assert "selling limit" in issue["title"].lower()
    assert "Seller Hub" in issue["fix"]


def test_an_expired_token_still_reads_as_an_account_problem():
    """21917053 is an expired IAF token — genuinely auth, and must stay that
    way. Guards against "fixing" it into a quota code on the strength of the
    mislabelled fixture in test_sync_quota.py."""
    exc = ebay_trading._failure(
        "AddFixedPriceItem",
        _root_with_message(""),
        [_error(code="21917053", long="Expired IAF token.")])
    assert "reconnect" in str(exc).lower()


def test_an_ordinary_rejection_still_reaches_its_field():
    """The code-keyed branches must not shadow the field-targeted ones that
    make the publish bar's fix-it chips work."""
    issue = ebay_errors.explain(
        {"errorId": 25002, "message": "The item specific Brand is missing."})
    assert issue["target"] == "specifics"


def test_the_refusal_names_the_aspect_it_is_about():
    """"specifics" points at a card holding forty inputs. The NAME is what
    lets the editor ring the one eBay actually refused, instead of leaving the
    seller to match a sentence against the grid."""
    issue = ebay_errors.explain(
        {"errorId": 25002,
         "message": "The item specific Sleeve Length is missing.",
         "parameters": [{"name": "0", "value": "Sleeve Length"}]})
    assert issue["target"] == "specifics"
    assert issue["fields"] == ["Sleeve Length"]
    assert "Sleeve Length" in issue["title"]


def test_a_rejection_that_names_no_aspect_claims_none():
    """An empty list, never a guess: a ring on the wrong field is worse than
    no ring at all."""
    issue = ebay_errors.explain(
        {"errorId": 25002, "message": "Please add the required item specifics."})
    assert issue["target"] == "specifics"
    assert issue["fields"] == []


# --- helpers ----------------------------------------------------------------

def _error(*, code: str, long: str):
    from xml.etree import ElementTree as ET
    el = ET.Element("Errors")
    ET.SubElement(el, "ErrorCode").text = code
    ET.SubElement(el, "LongMessage").text = long
    return el


def _root_with_message(message: str):
    from xml.etree import ElementTree as ET
    root = ET.Element("Response")
    ET.SubElement(root, "Message").text = message
    return root

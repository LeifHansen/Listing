"""What eBay is actually sent when a seller offers their watchers a discount.

"Send offers to interested buyers" is eBay's Negotiation API, and it is a
narrow contract with several ways to be wrong that all look the same from the
outside — a refusal on one listing, in a bulk run, with nothing on screen to
say why. So this file pins the wire.

The three that matter, each of them a documented eBay refusal rather than a
preference:

  * ONE LISTING PER CALL. `offeredItems` is an array and eBay accepts exactly
    one element in it (error 150005). A send that batched the group into one
    request would be refused wholesale — the seller presses the button and
    nothing happens to any of it.
  * THE DURATION IS NOT OURS. eBay's offer window is site-specific (4 days on
    EBAY_US and EBAY_GB, 2 on most others) and it refuses any other value
    (150027). Naming one would break this feature on whichever marketplaces we
    guessed wrong, so nothing is named and each site applies its own.
  * COUNTER-OFFERS ARE NOT IN THIS RELEASE. eBay documents `allowCounterOffer`
    as a field you must currently set to false. It is sent explicitly, so the
    day eBay allows them is a decision made in the code rather than a default
    quietly changing under sellers.

And the part that is about the seller rather than the API: a refusal has to
come back in words they can act on, and a listing eBay was never going to
carry an offer for is a SKIP, not a failure. Bulk runs work over a suggestion
group computed minutes ago; the listing that has since sold, or already has
an offer out on it, is an expected member of that group and not an alarm.
"""
from __future__ import annotations

import json

import httpx
import pytest

from backend.services import ebay_offers

CREDS = {"access_token": "tok"}


class _Client:
    """Stands in for httpx.Client, recording what was sent."""

    def __init__(self, response: httpx.Response):
        self.response = response
        self.sent: dict = {}

    def post(self, url, headers=None, json=None):  # noqa: A002 - httpx's name
        self.sent = {"url": url, "headers": headers or {}, "body": json or {}}
        return self.response

    def get(self, url, headers=None, params=None):
        self.sent = {"url": url, "headers": headers or {}, "params": params or {}}
        return self.response


def _reply(status: int, payload) -> httpx.Response:
    request = httpx.Request("POST", "https://api.ebay.com/x")
    if isinstance(payload, str):
        return httpx.Response(status, text=payload, request=request)
    return httpx.Response(status, json=payload, request=request)


def _refusal(code: int, message: str = "eBay says no") -> httpx.Response:
    return _reply(409, {"errors": [{"errorId": code, "message": message}]})


def _sent_ok() -> httpx.Response:
    return _reply(200, {"offers": [{"offerId": "5000123", "listingId": "110",
                                    "offerStatus": "PENDING"}]})


# --------------------------------------------------------------- the wire

def test_one_call_carries_exactly_one_listing():
    """eBay refuses an offer naming several listings (150005), so the array
    it insists on is an array of one."""
    client = _Client(_sent_ok())
    ebay_offers.send_offer(CREDS, "110", 10, client=client)
    items = client.sent["body"]["offeredItems"]
    assert len(items) == 1
    assert items[0]["listingId"] == "110"


def test_the_discount_travels_as_a_percentage_and_never_as_a_price():
    """eBay takes one or the other and refuses both together (150007). The
    percentage is also the only one of the two a bulk run can name once and
    still mean correctly across listings at different prices."""
    client = _Client(_sent_ok())
    ebay_offers.send_offer(CREDS, "110", 15, client=client)
    item = client.sent["body"]["offeredItems"][0]
    assert item["discountPercentage"] == "15"
    assert "price" not in item


def test_no_duration_is_named():
    """The sharp one. eBay's offer window is per-marketplace and it refuses
    anything but that site's own value (150027) — so a request that names 2
    days is a request that fails outright on the US and UK sites, where the
    window is 4. Sending nothing lets each site apply its own."""
    client = _Client(_sent_ok())
    ebay_offers.send_offer(CREDS, "110", 10, client=client)
    assert "offerDuration" not in client.sent["body"]


def test_counter_offers_are_explicitly_off():
    """eBay documents this as a field you must currently set to false. Sent
    rather than omitted, so enabling it is a decision someone makes."""
    client = _Client(_sent_ok())
    ebay_offers.send_offer(CREDS, "110", 10, client=client)
    assert client.sent["body"]["allowCounterOffer"] is False


def test_the_offer_is_for_one_item_unless_told_otherwise():
    """eBay's offer is all-or-nothing on the quantity named: an offer for the
    seller's whole stack of five is one a buyer who wants one cannot take."""
    client = _Client(_sent_ok())
    ebay_offers.send_offer(CREDS, "110", 10, client=client)
    assert client.sent["body"]["offeredItems"][0]["quantity"] == 1


def test_the_call_says_which_marketplace_it_is_for():
    """A required header on both Negotiation calls (missing it is 150001)."""
    client = _Client(_sent_ok())
    ebay_offers.send_offer(CREDS, "110", 10, client=client)
    assert client.sent["headers"]["X-EBAY-C-MARKETPLACE-ID"]
    assert client.sent["url"].endswith(
        "/sell/negotiation/v1/send_offer_to_interested_buyers")


def test_the_offer_id_comes_back():
    client = _Client(_sent_ok())
    out = ebay_offers.send_offer(CREDS, "110", 10, client=client)
    assert out == {"offer_id": "5000123", "status": "PENDING"}


def test_a_reply_without_an_offers_array_is_still_a_send():
    """eBay answered 200. Reading an unfamiliar body as a failure would have
    the seller send the same offer twice — and the second one refused as a
    negotiation already in progress."""
    client = _Client(_reply(200, {}))
    assert ebay_offers.send_offer(CREDS, "110", 10, client=client) == {
        "offer_id": "", "status": ""}


# ------------------------------------------------------------- the message

def test_a_note_rides_along_when_there_is_one():
    client = _Client(_sent_ok())
    ebay_offers.send_offer(CREDS, "110", 10, message="Thanks for watching!",
                           client=client)
    assert client.sent["body"]["message"] == "Thanks for watching!"


def test_an_empty_note_is_not_sent_as_an_empty_note():
    client = _Client(_sent_ok())
    ebay_offers.send_offer(CREDS, "110", 10, message="   ", client=client)
    assert "message" not in client.sent["body"]


def test_markup_is_taken_out_rather_than_refused_at_the_far_end():
    """eBay rejects a message containing HTML (150010), and it rejects the
    whole offer with it. A seller who typed an angle bracket loses the
    bracket, not the sale."""
    assert ebay_offers.clean_message("<b>50% off</b> today") == "b50% off/b today"


def test_a_long_note_is_trimmed_to_what_ebay_takes():
    note = ebay_offers.clean_message("x" * 3000)
    assert len(note) == ebay_offers.MESSAGE_MAX


# ------------------------------------------------------ the discount itself

def test_a_discount_below_ebays_minimum_is_refused_here():
    """eBay's floor is 5% (150008). Refused at the boundary with a sentence a
    seller can act on, rather than one failed call per listing."""
    with pytest.raises(ValueError) as exc:
        ebay_offers.validate_discount(4)
    assert "5" in str(exc.value)


def test_the_minimum_itself_is_allowed():
    assert ebay_offers.validate_discount(5) == 5.0


def test_a_slipped_decimal_is_refused():
    """Ours, not eBay's: a buyer takes one of these with a single tap, so an
    accidental 90% is a sale at that number."""
    with pytest.raises(ValueError):
        ebay_offers.validate_discount(90)


def test_nothing_at_all_is_refused():
    with pytest.raises(ValueError):
        ebay_offers.validate_discount(None)


def test_a_fraction_is_never_rounded_up_into_range():
    """4.4% rounds to 4 and is refused, rather than becoming the 5% eBay
    would accept — which is a bigger discount than the seller asked for."""
    with pytest.raises(ValueError):
        ebay_offers.validate_discount(4.4)


# --------------------------------------------------------- what eBay refuses

@pytest.mark.parametrize("code", [150011, 150017, 150018, 150019, 150020])
def test_a_listing_that_was_never_going_to_take_an_offer_is_a_skip(code):
    """Sold, ended, already negotiating, nobody watching: every one of these
    is an ordinary member of a suggestion group computed minutes ago, and
    reporting them as failures would make a working run look broken."""
    client = _Client(_refusal(code))
    with pytest.raises(ebay_offers.OfferRefused) as exc:
        ebay_offers.send_offer(CREDS, "110", 10, client=client)
    assert ebay_offers.skippable(exc.value) is True


def test_the_reason_is_in_words_the_seller_can_act_on():
    client = _Client(_refusal(150018, "Offer is invalid. A best offer "
                                      "currently exists on the listing 110."))
    with pytest.raises(ebay_offers.OfferRefused) as exc:
        ebay_offers.send_offer(CREDS, "110", 10, client=client)
    assert str(exc.value) == ("A buyer already has an offer in on that "
                              "listing — answer it first.")


def test_running_out_of_offers_for_the_day_is_not_a_skip():
    """eBay caps how many offers an account may send (150023). Counted as a
    failure on purpose: the rest of the run is not going to work either, and
    a seller told "12 skipped" would try again and again."""
    client = _Client(_refusal(150023))
    with pytest.raises(ebay_offers.OfferRefused) as exc:
        ebay_offers.send_offer(CREDS, "110", 10, client=client)
    assert ebay_offers.skippable(exc.value) is False
    assert "tomorrow" in str(exc.value)


def test_a_refusal_this_app_is_at_fault_for_arrives_in_ebays_own_words():
    """150007 (both price and percentage) is a bug in the request, not
    something about the listing. Dressing it up as a listing problem would
    hide it; eBay's own sentence in the log is what finds it."""
    client = _Client(_refusal(150007, "Both offer price and discount "
                                      "percentage cannot be present."))
    with pytest.raises(ebay_offers.OfferRefused) as exc:
        ebay_offers.send_offer(CREDS, "110", 10, client=client)
    assert "discount percentage" in str(exc.value)
    assert ebay_offers.skippable(exc.value) is False


def test_an_unreadable_refusal_still_says_something():
    client = _Client(_reply(409, "<html>gateway</html>"))
    with pytest.raises(ebay_offers.OfferRefused) as exc:
        ebay_offers.send_offer(CREDS, "110", 10, client=client)
    assert "409" in str(exc.value)


def test_a_token_that_cannot_make_the_call_says_so_separately():
    """"Reconnect eBay" is the one failure a seller can do something about,
    so it is told apart from every other refusal."""
    client = _Client(_reply(403, {"errors": [{"errorId": 1100,
                                              "message": "Insufficient scope"}]}))
    with pytest.raises(ebay_offers.ScopeError):
        ebay_offers.send_offer(CREDS, "110", 10, client=client)


def test_no_token_is_never_a_silent_success():
    with pytest.raises(ebay_offers.ScopeError):
        ebay_offers.send_offer(None, "110", 10)


# --------------------------------------------------- who is eligible at all

def test_eligibility_is_ebays_answer_and_not_ours():
    """Watchers are one signal of "interested"; eBay counts others and is the
    only party that knows them. Asking it once is also what keeps a bulk run
    from spending a call per listing to be told 150020."""
    client = _Client(_reply(200, {"eligibleItems": [{"listingId": "110"},
                                                    {"listingId": "220"}]}))
    assert ebay_offers.eligible_items(CREDS, client=client) == {"110", "220"}
    assert client.sent["url"].endswith("/sell/negotiation/v1/find_eligible_items")


def test_nothing_eligible_is_an_empty_answer_not_a_failure():
    """eBay answers 204 when it has nothing — an empty set, and the run
    reports every listing as skipped rather than erroring."""
    client = _Client(httpx.Response(
        204, request=httpx.Request("GET", "https://api.ebay.com/x")))
    assert ebay_offers.eligible_items(CREDS, client=client) == set()


def test_a_sweep_that_cannot_be_read_raises_rather_than_answering_nobody():
    """An empty set and a failed read mean opposite things to the caller: one
    says "no listing has interested buyers", the other "we could not ask".
    Collapsing them would report a whole store as skipped."""
    client = _Client(_reply(500, {"errors": [{"errorId": 150000}]}))
    with pytest.raises(RuntimeError):
        ebay_offers.eligible_items(CREDS, client=client)


def test_an_unscoped_sweep_asks_for_a_reconnect():
    client = _Client(_reply(401, "invalid access token"))
    with pytest.raises(ebay_offers.ScopeError):
        ebay_offers.eligible_items(CREDS, client=client)


def test_a_disconnected_seller_has_nothing_eligible():
    assert ebay_offers.eligible_items(None) == set()
    assert ebay_offers.eligible_items({}) == set()


def test_the_sweep_asks_for_a_page_ebay_will_serve():
    """eBay caps `limit` at 200 and refuses anything above it (150003)."""
    client = _Client(_reply(200, {"eligibleItems": []}))
    ebay_offers.eligible_items(CREDS, client=client)
    assert int(client.sent["params"]["limit"]) <= 200


def test_an_item_without_a_listing_id_is_not_a_listing_id():
    """Defensive: a malformed row must not put the string "None" into the set
    that decides which listings get offered."""
    client = _Client(_reply(200, {"eligibleItems": [{"listingId": "110"}, {},
                                                    {"listingId": ""}]}))
    assert ebay_offers.eligible_items(CREDS, client=client) == {"110"}


def test_the_request_body_is_json_ebay_will_parse():
    """Belt and braces on the shape as a whole: whatever else changes, what
    goes out is a JSON object carrying the array eBay documents."""
    client = _Client(_sent_ok())
    ebay_offers.send_offer(CREDS, "110", 10, message="hello", client=client)
    body = json.loads(json.dumps(client.sent["body"]))
    assert set(body) == {"offeredItems", "allowCounterOffer", "message"}

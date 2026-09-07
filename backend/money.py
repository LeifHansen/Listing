"""What shape an amount takes: how it is written, and where a price lands.

One rule, in one place, because the second copy is where these drift — and
they had already: `listing_merge` had it right and kept it private, while
`notifications.notify_sold` hardcoded a dollar sign into the sentence a seller
uses to decide whether a sale was worth shipping. `Listing.currency` is a real
field that the editor sets and eBay reports sales in, so a seller on
eBay.co.uk was told their £45 item sold "for $45.00".

USD keeps the symbol because that is what the great majority of this app's
sellers see and "$45.00" is unambiguous to them. Everything else gets the
amount and the ISO code rather than a symbol: "€45.00" invites guessing at
which of several euro-adjacent currencies was meant, and several currencies
share "$" outright, while "45.00 EUR" cannot be misread.

Stdlib only, and no dependency on anything in this package, so any module that
shows an amount can use it.
"""
from __future__ import annotations

from typing import Optional


def money(amount, currency: Optional[str] = "USD") -> Optional[str]:
    """`amount` written in `currency`, or None when there is no amount.

    None rather than "$0.00": an amount that could not be read is not zero,
    and a caller that has nothing to show should say nothing rather than
    report a free item.
    """
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return None
    code = (str(currency or "").strip() or "USD").upper()
    return f"${value:,.2f}" if code == "USD" else f"{value:,.2f} {code}"


# The floor a charm price can land on: eBay's own practical minimum, and the
# same number bulk_actions.MIN_PRICE stops a percentage cut at.
MIN_CHARM_PRICE = 0.99


def charm_price(amount) -> Optional[float]:
    """`amount` moved to the nearest price ending in .99.

    Every price this app CHOOSES goes through here — the AI's drafted price,
    the market number that overrules a draft priced far under it, the headline
    comp suggestion, and a bulk percentage cut — so a draft worth about $25
    lists at $24.99 rather than $25.00. It is what a seller would have typed
    themselves, and it is the whole of the rule: a price the seller types into
    the editor is theirs, and nothing here rewrites it.

    Nearest, not always down: $22.50 becomes $22.99, because the app's own
    pricing rule elsewhere is never to shave a price "to be safe" — that sells
    the item cheap. The move is never more than half a dollar in either
    direction, a value exactly between two charm points goes to the higher of
    them, and anything under $0.99 comes up to it.

    Returns None when there is no usable amount, the same answer `money()`
    gives: an amount that could not be read is not a price, and neither is
    zero or a negative.
    """
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    # Charm points sit one cent under a whole dollar, so shifting up by that
    # cent turns "nearest .99" into "nearest whole dollar", which is integer
    # arithmetic rather than float rounding that can land on 24.990000000001.
    cents = int(round(value * 100))
    nearest = ((cents + 51) // 100) * 100 - 1
    return round(max(nearest, int(MIN_CHARM_PRICE * 100)) / 100, 2)

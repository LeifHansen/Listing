"""How an amount is written when the seller reads it.

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

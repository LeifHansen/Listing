"""A refusal to a dry run is the answer to a question, not a second failure.

eBay's Verify calls create nothing; the app asks one only to diagnose a
refusal it already has — ebay_account.probe_block_scope re-puts a listing that
drew error 240 to learn whether the account or the listing is the cause, and
logs what it concluded. Every dry run it made was ALSO logged at WARNING by the
Trading client, which is where error capture starts, and because the probe runs
inside the failed publish's `except`, the capture handler attached that live
traceback and graded each one high. Production's feed on 2026-09-24 carried the
publish refusal x18 and, beside it as a separate "bug", the probe's own
questions x9.

The real call's refusal still warns: that one is the failure.
"""
from __future__ import annotations

import logging

import pytest

from backend.services import ebay_trading

NS = "urn:ebay:apis:eBLBaseComponents"

E240 = ("The item cannot be listed or modified. The title and/or description "
        "may contain improper words, or the listing or seller may be in "
        "violation of eBay policy.")


class _Resp:
    status_code = 200

    def __init__(self, content):
        self.content = content


def _refusal(call: str) -> bytes:
    return (f'<?xml version="1.0" encoding="utf-8"?>'
            f'<{call}Response xmlns="{NS}"><Ack>Failure</Ack>'
            f"<Errors><SeverityCode>Error</SeverityCode>"
            f"<ShortMessage>Listing violates policy.</ShortMessage>"
            f"<LongMessage>{E240}</LongMessage>"
            f"<ErrorCode>240</ErrorCode></Errors>"
            f"</{call}Response>").encode()


def _refuse(monkeypatch, caplog, call: str) -> list[logging.LogRecord]:
    monkeypatch.setattr(ebay_trading.httpx, "post",
                        lambda *a, **k: _Resp(_refusal(call)))
    with caplog.at_level(logging.INFO, logger="thryft"):
        with pytest.raises(ebay_trading.TradingError) as info:
            ebay_trading._call(call, "tok", "<Item/>")
    assert info.value.code == "240"
    return [r for r in caplog.records if "rejected" in r.getMessage()]


@pytest.mark.parametrize("call", ["VerifyAddFixedPriceItem", "VerifyAddItem"])
def test_a_dry_run_refusal_is_logged_below_error_capture(monkeypatch, caplog,
                                                         call):
    lines = _refuse(monkeypatch, caplog, call)
    assert len(lines) == 1, "the refusal is still in the log, in full"
    assert lines[0].levelno == logging.INFO, (
        "a diagnostic dry run's refusal was filed as a production error")


@pytest.mark.parametrize("call", ["AddFixedPriceItem", "AddItem",
                                  "ReviseFixedPriceItem"])
def test_a_real_refusal_still_warns(monkeypatch, caplog, call):
    lines = _refuse(monkeypatch, caplog, call)
    assert len(lines) == 1
    assert lines[0].levelno == logging.WARNING

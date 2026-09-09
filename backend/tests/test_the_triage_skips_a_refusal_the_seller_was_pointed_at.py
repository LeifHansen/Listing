"""The daily triage does not propose a pull request for a seller's data gap.

Every publish or revise eBay refuses is logged, and the log line's TEMPLATE
now carries where the refusal points (ebay_provider.refusal_key), so one kind
of refusal is one row in the error feed rather than every refusal being one
row whose message was whichever came last. The triage script reads that token:
a refusal over a field the seller was shown — a missing Inseam, a Size not on
eBay's list — is their listing's gap, and it is skipped. One the classifier
did not recognise ("generic"), a 240 eBay would not explain ("unexplained"),
and an account block are what a pull request might fix, so those stay.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from backend.marketplaces.ebay_provider import refusal_key

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def triage():
    path = REPO_ROOT / ".github" / "scripts" / "triage_errors.py"
    spec = importlib.util.spec_from_file_location("triage_errors", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(message: str) -> dict:
    return {"fingerprint": "abcd1234abcd1234", "severity": "medium",
            "exc_type": "TradingError", "message": message, "count": 26,
            "module": "backend.marketplaces.ebay_provider", "func": "publish"}


@pytest.mark.parametrize("message", [
    "revise (imported) refused over specifics[Inseam]: Missing required item specific: Inseam | session=<id>",
    "trading publish refused over specifics[Size]: “W” isn’t one of eBay’s options for Size | session=<id>",
    "trading publish refused over title: eBay refused the title | session=<id>",
])
def test_a_field_level_refusal_is_the_sellers(triage, message):
    assert triage.why_not(_row(message), {}, str(REPO_ROOT))


@pytest.mark.parametrize("message", [
    "trading publish refused over generic: eBay rejected the listing | session=<id>",
    "trading publish refused over unexplained: eBay refused this listing and wouldn't say why | session=<id>",
    "trading publish refused over account: eBay's reason: … | session=<id>",
    "trading publish refused over unclassified: ? | session=<id>",
])
def test_one_the_app_could_not_explain_is_still_ours(triage, message):
    assert triage.why_not(_row(message), {}, str(REPO_ROOT)) is None


def test_the_key_names_the_target_and_its_aspects():
    assert refusal_key([{"target": "specifics", "fields": ["Inseam"]}]) == "specifics[Inseam]"
    assert refusal_key([{"target": "title"}]) == "title"
    assert refusal_key([{"target": "account", "placeholder": True}]) == "unexplained"
    assert refusal_key([]) == "unclassified"
    # Nothing per-occurrence: the value refused is in the title, not the key.
    assert "W" not in refusal_key([{"target": "specifics", "fields": ["Size"],
                                    "title": "“W” isn’t one of eBay’s options"}])

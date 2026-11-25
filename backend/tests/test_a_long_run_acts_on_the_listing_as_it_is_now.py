""""Finish everything" judges each listing when it reaches it, not when pressed.

The fill jobs are handed the records read when the button was pressed, and
"Finish everything" is uncapped -- one vision pass per listing, so the last
one can be reached hours later. Every other bulk worker re-reads through
_owned_by; these two acted on the captured copy, so a listing that:

  * SOLD in the meantime had its photos re-downloaded and was written back
    with the old status -- back in Active, and charged for;
  * was DELETED came back, because writing a missing row inserts it (the
    notes half used `db.get_listing(rid) or rec`, handing on the stale copy).
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend import main  # noqa: E402

UID = "u-stale-run"


def _body(rid: str) -> dict:
    return {"title": f"Item {rid}", "category_id": "11450",
            "images": ["a.jpg"], "missing_info": ["exact measurements"]}


def test_a_listing_that_sold_or_went_mid_run_is_left_alone(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    charged = []
    monkeypatch.setattr(main.deps, "charge_uid",
                        lambda *a, **k: charged.append(a) or None)
    for rid in ("sold1", "gone1"):
        assert dbmod.upsert_listing(rid, _body(rid), status="published",
                                    user_id=UID)
    captured = [dbmod.get_listing("sold1"), dbmod.get_listing("gone1")]

    # Between the press and the run reaching them:
    assert dbmod.upsert_listing("sold1", _body("sold1"), status="sold",
                                user_id=UID)
    assert dbmod.delete_listing("gone1", user_id=UID)

    main._run_finish_job("job-stale", captured, UID,
                         {"access_token": "t"}, "http://x")

    assert dbmod.get_listing("gone1") is None, "a deleted listing came back"
    assert dbmod.get_listing("sold1")["status"] == "sold"
    assert not charged

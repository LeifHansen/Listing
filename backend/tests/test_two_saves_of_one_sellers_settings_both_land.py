"""Two saves into one seller's `prefs` at once must not lose either.

`prefs` holds several independent things in one JSON column -- the Settings
defaults, the saved listing views, the duplicate dismissals, the background
removal switch -- and save_prefs merges into it with a read-modify-write. The
read took no lock, so two saves that overlapped (a view saved in one tab, a
duplicate dismissed in another) both merged into the SAME old copy, and the
second commit erased the first one's key. Reproduced against Postgres: two
overlapping saves of different keys left only one of them stored.

The fix is the row lock mutate_listing_data already takes for the same
reason. SQLite has no FOR UPDATE (it serialises writers instead), so the
suite checks the read asks for the lock; the race itself needs Postgres.
"""
from __future__ import annotations


def test_the_merge_reads_the_row_under_a_lock(dbmod, monkeypatch):
    dbmod.create_user("u-prefs", "prefs@example.com", "x")
    real_get = dbmod.Session.get
    asked: list[bool] = []

    def _get(self, entity, ident, **kw):
        if entity is dbmod.User:
            asked.append(bool(kw.get("with_for_update")))
        return real_get(self, entity, ident, **kw)

    monkeypatch.setattr(dbmod.Session, "get", _get)
    dbmod.save_prefs("u-prefs", {"listing_views": [{"id": "v"}]})

    assert asked == [True]


def test_saves_of_different_keys_still_merge(dbmod):
    dbmod.create_user("u-prefs", "prefs@example.com", "x")
    dbmod.save_prefs("u-prefs", {"listing_views": [{"id": "v"}]})
    dbmod.save_prefs("u-prefs", {"duplicates_dismissed": {"fp": "t"}})
    assert sorted(dbmod.get_prefs("u-prefs")) == ["duplicates_dismissed",
                                                 "listing_views"]

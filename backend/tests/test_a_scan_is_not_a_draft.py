"""A Shop Mode scan is not a draft, and never was one the seller asked for.

Scanning is how the app earns its keep on a thrift run: point the camera at a
thing, get an ID and what it sells for, decide. Most of that decision is "no"
— eight of ten items go back on the shelf — and the two that don't are the
only ones the seller ever wanted a listing for.

`/api/identify` wrote every one of them as `status="draft"`, and every drafts
filter in the app counts that status. So a seller who scanned ten items came
home to eight drafts of objects they had deliberately not bought, in the strip
the Sell screen opens on, indistinguishable from real work in progress. The
row itself is not the mistake — it binds the session id to its owner before
any media URL carrying that id exists, which is what stops a leaked id being
claimed by another account — the STATUS was.

So a scan is `db.SCANNED`: written, owned, and invisible to every
seller-facing read until "Buy" promotes it to `unlisted` and it appears under
Finds. Two halves, and both are load-bearing:

  * the read excludes it BY DEFAULT rather than by each caller's allowlist.
    Five separate seller-facing surfaces read this table with no status filter
    (the grid, the CSV export, insights, finish-all, the message fan-out), and
    an omission list means the next one leaks by forgetting;
  * it is excluded in SQL, not after the fetch. This read is paged, and
    dropping rows from a page in Python makes `limit + 1` mis-report whether
    there are more and makes a keyset cursor skip.

`include_scanned=True` is the way back to every row a user owns, which is
what deletion and any future migration need — a scan the seller never saw is
still theirs, and must still be destroyed with the account.
"""
from __future__ import annotations

import datetime as _dt

import pytest

pytest.importorskip("sqlalchemy")


def _ids(rows):
    return [r["id"] for r in rows]


def _cursor(row):
    """The keyset cursor for a row, the way /api/listings builds one.

    A record carries `updated_at` as an ISO STRING (_record_to_dict) and
    `list_listings` compares it against a timestamp column, so handing the
    string straight back does not move the page edge — the same page answers
    forever. main._cursor_from is what does this in the app, including
    reading a naive stamp as UTC rather than local.
    """
    when = _dt.datetime.fromisoformat(row["updated_at"])
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return (when, row["id"])


# --- the default read --------------------------------------------------------

def test_a_scan_stays_out_of_the_sellers_pipeline(dbmod):
    dbmod.upsert_listing("scan-1", {"title": "A bowl someone put back"},
                         status=dbmod.SCANNED, user_id="u1")
    dbmod.upsert_listing("draft-1", {"title": "Real work in progress"},
                         status="draft", user_id="u1")

    assert _ids(dbmod.list_listings(user_id="u1")) == ["draft-1"]
    assert _ids(dbmod.list_listings_best_effort(user_id="u1")) == ["draft-1"]


def test_buying_the_scan_is_what_makes_it_appear(dbmod):
    """The promotion /api/inventory/add performs, on the same row id — which
    is what carries the scan's photos and its drafted listing across."""
    dbmod.upsert_listing("scan-1", {"title": "A bowl worth buying"},
                         status=dbmod.SCANNED, user_id="u1")
    assert dbmod.list_listings(user_id="u1") == []

    dbmod.upsert_listing("scan-1", {"title": "A bowl worth buying"},
                         status="unlisted", user_id="u1")

    rows = dbmod.list_listings(user_id="u1")
    assert _ids(rows) == ["scan-1"]
    assert rows[0]["status"] == "unlisted"      # the Finds tab's status


def test_an_explicit_allowlist_still_says_what_it_wants(dbmod):
    """A caller that named its statuses has already answered this question,
    and the console's cross-user browse is the one place a scan can be seen
    at all."""
    dbmod.upsert_listing("scan-1", {"title": "A bowl"},
                         status=dbmod.SCANNED, user_id="u1")

    assert _ids(dbmod.list_listings(user_id="u1",
                                    statuses=(dbmod.SCANNED,))) == ["scan-1"]
    # And a live-only read is untouched by any of this.
    assert dbmod.list_listings(user_id="u1", statuses=("published",)) == []


def test_every_row_a_user_owns_can_still_be_reached(dbmod):
    """The enumeration deletion needs. A scan the seller never saw is still
    their data, and "nothing survives" has to mean nothing — which a read
    that hides scans by default cannot be the one to check."""
    from backend import auth

    uid = dbmod.create_user("u-1", "gone@example.com",
                            auth.hash_password("hunter2hunter2"))["id"]
    dbmod.upsert_listing("scan-1", {"title": "A bowl"},
                         status=dbmod.SCANNED, user_id=uid)
    dbmod.upsert_listing("draft-1", {"title": "B"}, status="draft", user_id=uid)

    assert sorted(_ids(dbmod.list_listings(user_id=uid,
                                           include_scanned=True))) \
        == ["draft-1", "scan-1"]

    dbmod.delete_user(uid)
    assert dbmod.list_listings(user_id=uid, include_scanned=True) == []


def test_the_count_agrees_with_the_grid(dbmod):
    """The delete-account dialog's number. It counts in SQL while the grid
    lists in SQL, and the two reading differently is how a seller with twelve
    listings was told they were about to erase forty-seven."""
    for i in range(5):
        dbmod.upsert_listing(f"scan-{i}", {"title": f"put back {i}"},
                             status=dbmod.SCANNED, user_id="u1")
    dbmod.upsert_listing("kept-1", {"title": "bought"}, status="unlisted",
                         user_id="u1")
    dbmod.upsert_listing("live-1", {"title": "live"}, status="published",
                         user_id="u1")

    assert dbmod.count_listings("u1") == 2
    assert dbmod.count_listings("u1") == len(dbmod.list_listings(user_id="u1"))
    # An explicit allowlist is still exactly what it says.
    assert dbmod.count_listings("u1", statuses=("published",)) == 1
    # And the row is still there to be destroyed with the account.
    assert dbmod.count_listings("u1", include_scanned=True) == 7


# --- the page boundary -------------------------------------------------------

def test_a_page_is_not_short_because_of_what_it_hid(dbmod):
    """Filtered in SQL, so a page of three is three LISTINGS.

    Interleaved deliberately: a thrift run scans and buys in the same minutes,
    so the scans are not conveniently at one end. Dropping them from the page
    after the fetch would answer two rows here and report "that's all" — the
    seller's older listings falling off the end of a grid that looked full.
    """
    for i in range(6):
        dbmod.upsert_listing(f"scan-{i}", {"title": f"put back {i}"},
                             status=dbmod.SCANNED, user_id="u1")
        dbmod.upsert_listing(f"kept-{i}", {"title": f"bought {i}"},
                             status="unlisted", user_id="u1")

    page = dbmod.list_listings(limit=3, user_id="u1")
    assert len(page) == 3
    assert all(r["status"] == "unlisted" for r in page)

    # And the keyset cursor walks the rest without skipping or repeating one.
    # Bounded: a cursor that stops advancing answers the same page forever,
    # and a test that discovers that by hanging tells nobody which page.
    seen = list(_ids(page))
    for _ in range(10):
        page = dbmod.list_listings(limit=3, user_id="u1",
                                   before=_cursor(page[-1]))
        if not page:
            break
        seen.extend(_ids(page))
    else:
        raise AssertionError(f"the cursor never reached the end: {seen}")
    assert sorted(seen) == sorted(f"kept-{i}" for i in range(6))
    assert len(seen) == len(set(seen)), "a row was served twice"


# --- the route ---------------------------------------------------------------

@pytest.fixture
def app(monkeypatch):
    """backend.main with everything after the draft silenced — the same
    fixture shape as test_the_ais_confidence_reaches_the_draft_card."""
    pytest.importorskip("fastapi")
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend import main
    from backend.models import IdentifyResult, Listing

    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main, "_resolve_category", lambda *a, **k: None)
    monkeypatch.setattr(main, "_assign_store_category", lambda *a, **k: None)
    monkeypatch.setattr(main, "_enrich_listing", lambda *a, **k: {})
    monkeypatch.setattr(main, "_lookup_artwork", lambda *a, **k: None)
    monkeypatch.setattr(main, "_research_draft", lambda *a, **k: None)
    monkeypatch.setattr(main, "_price_against_comps", lambda *a, **k: None)
    monkeypatch.setattr(
        main.claude_ai, "identify",
        lambda paths, names, strategy="", notes="", item_notes="":
        IdentifyResult(listing=Listing(title="A bowl", images=list(names)),
                       confidence="high", raw_observations=""))
    return main


def _photos(dir_):
    import io

    from PIL import Image

    dir_.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), (240, 240, 240)).save(buf, "JPEG")
    (dir_ / "src_000.jpg").write_bytes(buf.getvalue())


def test_the_scan_route_writes_a_scan(app, monkeypatch):
    """What /api/identify actually records. It is Shop Mode's route alone —
    the pipeline drafts through /api/identify-async — so this status is the
    one thing standing between a thrift run and eight phantom drafts."""
    from fastapi.testclient import TestClient

    written: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app.db, "upsert_listing",
        lambda lid, listing, status="draft", user_id=None, when=None:
        written.append((lid, status)) or True)

    session_id = app.storage.new_session_id()
    _photos(app.storage.optimized_dir(session_id))

    # NOT `with TestClient(...)`: the context manager runs the app's lifespan,
    # and that starts errorlog's background writer (main.start_writer) as a
    # daemon thread that outlives this test and every later one in the
    # session. It then races their flush() for the same queue -- conftest
    # drains the queue between tests but cannot un-start the thread. This
    # route needs no startup, so it does not pay for one.
    resp = TestClient(app.app).post(f"/api/identify/{session_id}")

    assert resp.status_code == 200, resp.text
    assert written == [(session_id, app.db.SCANNED)]

"""A seller saved two good pages and the settings screen called them dead.

One said `HTTP 429`. The other said "that page had nothing usable on it — a
login wall, a paywall, or a page about something else". Neither was true, and
both turned the reference OFF, so a link that worked fine stopped teaching the
expert and the seller had no way to make it read the page again.

What had actually happened:

  * 429 is a museum saying "not so fast", which is a fact about the minute we
    asked in and not about the page. It was recorded as a dead link.
  * The other page answered perfectly well, behind a consent prompt and an ad
    slot. The reader handed the banner to the summariser, the summariser said
    there was nothing there -- correctly, about the banner -- and that shrug
    was written down as a paywall on the seller's page.

And two more that reached the same sentence: a site that refuses an unknown
client outright, and our own summariser being unavailable. Both are "we have
not seen the page", which is never a verdict on it.

So this file pins the distinctions rather than the wording: what gets RETRIED,
what stays ENABLED, and that the reason shown to the seller is one we can
actually stand behind.
"""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from backend.services import reference_fetch as rf  # noqa: E402


# --- busy is not broken -----------------------------------------------------

def test_a_rate_limit_is_a_moment_not_a_verdict():
    """The whole reason 429 needs its own class: the caller has to be able to
    tell "come back in a minute" from "this link is no good"."""
    assert isinstance(rf._refusal(429), rf.TemporaryFailure)
    assert isinstance(rf._refusal(503), rf.TemporaryFailure)
    # ...and still a ValueError, so every existing caller keeps catching it.
    assert isinstance(rf._refusal(429), ValueError)
    assert "429" in str(rf._refusal(429))


def test_a_site_refusing_this_reader_is_not_a_verdict_either():
    for status in (401, 403, 451):
        assert isinstance(rf._refusal(status), rf.Blocked)
    # A 404 is about the page, and stays an ordinary failure.
    assert type(rf._refusal(404)) is ValueError


def test_the_sites_own_retry_after_beats_any_backoff_we_invent():
    assert rf._retry_after("5") == 5.0
    assert rf._retry_after("") is None
    assert rf._retry_after("not a number") is None
    # An HTTP date, which is the other spelling in the RFC.
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    soon = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert 20 <= (rf._retry_after(format_datetime(soon)) or 0) <= 40
    # A date in the past is not a negative wait.
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    assert rf._retry_after(format_datetime(past)) == 0.0


def test_no_wait_is_unbounded_however_long_the_site_asks_for():
    """A site is allowed to say "come back tomorrow". We are not allowed to
    hold a background thread until then."""
    assert rf._pause("86400", 0) == rf.MAX_PAUSE
    assert rf._pause("", 0) >= 0.5
    assert rf._pause("", 5) <= rf.MAX_PAUSE
    assert rf.ATTEMPTS * rf.MAX_PAUSE <= 60


def test_a_busy_site_is_asked_again_and_then_reported_as_busy(monkeypatch):
    """The end-to-end shape of the Met's 429: three goes, the site's own
    Retry-After honoured, and what comes out says "busy", not "dead"."""
    import httpx

    seen = []
    slept = []
    monkeypatch.setattr(rf.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(rf, "check",
                        lambda url: (url, ["93.184.216.34"]))

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(429, headers={"retry-after": "1"}, text="slow down")

    _pin_transport(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(rf.TemporaryFailure) as caught:
        rf.fetch("https://www.metmuseum.org/art/collection/search")
    assert len(seen) == rf.ATTEMPTS, "a 429 is worth asking again"
    assert slept == [1.0, 1.0], "and the site's own Retry-After is what we wait"
    assert "429" in str(caught.value)


def test_a_busy_site_that_recovers_is_simply_read(monkeypatch):
    import httpx

    calls = {"n": 0}
    monkeypatch.setattr(rf.time, "sleep", lambda s: None)
    monkeypatch.setattr(rf, "check", lambda url: (url, ["93.184.216.34"]))

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "1"})
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<html><body><p>Hallmarks</p></body></html>")

    _pin_transport(monkeypatch, httpx.MockTransport(handler))
    assert "Hallmarks" in rf.fetch("https://example.test/marks")


def _pin_transport(monkeypatch, transport):
    """Make every client fetch builds use `transport`.

    fetch() constructs its own client on purpose -- see the module docstring
    on pinning -- so the seam is the class, not an argument.
    """
    import httpx

    real = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


# --- a wall is not a paywall ------------------------------------------------

CONSENT_WALLED = """
<html><head><title>Fenton Glass Marks</title>
<meta name="description" content="Dating Fenton glass from its mark."></head>
<body>
<div id="onetrust-consent-sdk" class="cookie-consent-banner">
  <h2>We use cookies</h2>
  <p>We and our 412 partners store and access information on your device.</p>
  <button>Accept all cookies</button></div>
<div class="ad-slot leaderboard">Advertisement</div>
<div class="newsletter-modal"><p>Subscribe to our weekly digest!</p></div>
<nav><a href="/">Home</a></nav>
<main><article><h1>Dating Fenton glass</h1>
<p>The oval Fenton logo was moulded into the glass from 1970. A number added
   inside the logo marks the decade: 8 for the 1980s, 9 for the 1990s.</p>
<p>Pieces made before 1970 carry a paper label only, so an unmarked piece is
   not by itself evidence of a reproduction.</p></article></main>
<footer>Privacy · Cookies</footer></body></html>"""


def test_the_page_under_the_popup_is_what_gets_read():
    """The one the seller complained about. The banner, the ad slot and the
    newsletter overlay come off; what the page actually says survives."""
    text = rf.readable_text(CONSENT_WALLED)
    assert "moulded into the glass from 1970" in text
    assert "paper label" in text
    for furniture in ("Accept all cookies", "412 partners", "Advertisement",
                      "Subscribe to our weekly"):
        assert furniture not in text, f"{furniture} is not the page"


def test_the_title_and_description_lead_because_they_say_what_it_is():
    text = rf.readable_text(CONSENT_WALLED)
    assert text.startswith("Fenton Glass Marks")
    assert "Dating Fenton glass from its mark." in text


def test_a_page_that_was_read_is_not_reported_as_a_wall():
    """The gate that keeps this honest. A short page is still a page, and
    saying otherwise is the same mistake in the other direction."""
    assert rf.unreadable_reason(CONSENT_WALLED,
                               rf.readable_text(CONSENT_WALLED)) == ""


@pytest.mark.parametrize("page,expected", [
    ("""<html><head><title>Just a moment...</title></head><body>
        <div class="cf-browser-verification">Checking your browser…</div>
        </body></html>""", "bot check"),
    ("""<html><body><div id="onetrust-consent-sdk"><h2>We use cookies</h2>
        <button>Accept all cookies</button></div></body></html>""",
     "privacy prompt"),
    ("""<html><body><div id="root"></div>
        <script id="__NEXT_DATA__" type="application/json">{"a":1}</script>
        </body></html>""", "browser"),
    ("""<html><body><div class="paywall">Subscribe to continue reading.</div>
        </body></html>""", "sign in or subscribe"),
])
def test_what_was_in_the_way_is_named_rather_than_guessed_at(page, expected):
    """Not the wording -- the fact that the four cases are told apart. The
    sentence they all used to share named a paywall on three pages that did
    not have one."""
    reason = rf.unreadable_reason(page, rf.readable_text(page))
    assert expected in reason
    assert "paywall" not in reason or expected == "sign in or subscribe"


def test_a_page_that_draws_itself_still_gives_up_its_catalogue_data():
    """A collection record whose text arrives by JavaScript still ships the
    facts in its JSON-LD, and that is the material the link was saved for."""
    page = """<html><head><title>Vase</title>
    <script type="application/ld+json">{"@type":"CreativeWork",
      "name":"Blue-and-white porcelain vase",
      "dateCreated":"Qing dynasty, Yongzheng period (1723-35)",
      "description":"Six-character mark in underglaze blue on the base.",
      "image":"https://example.test/a.jpg"}</script></head>
    <body><div id="root"></div></body></html>"""
    text = rf.readable_text(page)
    assert "Six-character mark" in text
    assert "Yongzheng" in text
    # ...and the image URL is not prose.
    assert "https://example.test/a.jpg" not in text


def test_a_page_wrapped_in_something_called_an_overlay_is_not_lost():
    """The stripper is aggressive, so it needs a floor: a site that names its
    outermost container "page-overlay" must not come back empty."""
    page = ("<html><body><div class='page-overlay'><p>" + "Hallmark facts. " * 40
            + "</p></div></body></html>")
    assert "Hallmark facts." in rf.readable_text(page)


def test_a_thin_page_is_not_worth_a_model_call():
    assert rf.too_thin("") is True
    assert rf.too_thin("Accept all cookies") is True
    assert rf.too_thin("x" * 4000) is False


# --- and the headers that get a page at all ---------------------------------

def test_the_reader_presents_itself_the_way_a_browser_does():
    """A bare "ThryftShop/1.0" with a one-line Accept is refused by every WAF
    on the internet, and the pages sellers save sit behind one. One GET, at
    human pace, for a page its owner chose -- so it asks the way they would."""
    headers = rf._reader_headers()
    assert "Mozilla/5.0" in headers["User-Agent"]
    # ...and still says who it is, for whoever reads the log.
    assert "thryft" in headers["User-Agent"].lower()
    assert "Accept-Language" in headers
    assert "text/html" in headers["Accept"]


def test_nothing_in_the_headers_carries_identity():
    """The SSRF rules do not bend for a 403: no session, no credentials, and
    no Referer telling a third party where the seller has been."""
    for name in rf._reader_headers():
        assert name.lower() not in ("cookie", "authorization", "referer")


def test_the_user_agent_can_be_overridden_without_a_deploy(monkeypatch):
    import importlib
    monkeypatch.setenv("REFERENCE_USER_AGENT", "PoliteBot/2.0 (+https://x.test)")
    reloaded = importlib.reload(rf)
    try:
        assert reloaded.USER_AGENT == "PoliteBot/2.0 (+https://x.test)"
    finally:
        monkeypatch.delenv("REFERENCE_USER_AGENT", raising=False)
        importlib.reload(reloaded)

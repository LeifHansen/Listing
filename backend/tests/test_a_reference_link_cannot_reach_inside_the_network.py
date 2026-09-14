"""The app now fetches a URL somebody typed. From inside production.

That is a server-side request forgery primitive, and it arrived with the
reference links: the owner saves a URL to teach an expert, and something has
to go and read it. The ways it goes wrong are not obvious, which is why they
are enumerated here rather than left to a code reviewer's memory:

  * https://169.254.169.254/ is the cloud metadata endpoint. On most
    providers it hands out instance credentials to anything that asks.
  * 10.0.0.5, 127.0.0.1, ::1, fc00::/7 -- the private network this app is
    inside, including every internal service that trusts its own network.
  * ::ffff:10.0.0.1 -- a private IPv4 address wearing an IPv6 costume.
  * 100.64.0.0/10 -- shared address space that several clouds route internal
    traffic over, and which Python's own `is_private` does NOT flag.
  * A PUBLIC host that redirects to a private one. Checking the URL the user
    typed and then following redirects checks the one address an attacker
    does not need to control.
  * A response that never ends, or ends at forty gigabytes.

The module's own docstring is honest about the one hole it does not fully
close -- a resolver an attacker controls, answering differently between the
check and the connect. `_pinned_url` narrows it to the point where the
remaining exposure is a GET whose body is then summarised and fenced, and the
comment says so rather than implying the hole is shut. This file pins
everything that IS closed.
"""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from backend.services import reference_fetch as rf  # noqa: E402


# --- the addresses that must never be fetched from --------------------------

@pytest.mark.parametrize("address", [
    "169.254.169.254",        # the cloud metadata endpoint
    "127.0.0.1", "127.0.0.53",
    "10.0.0.5", "192.168.1.1", "172.16.0.1",
    "0.0.0.0",
    "::1", "::",
    "fc00::1",                # IPv6 unique-local
    "fe80::1",                # IPv6 link-local
    "::ffff:10.0.0.1",        # a private IPv4 inside an IPv6
    "::ffff:169.254.169.254",  # ...and the metadata endpoint inside one
    "100.64.0.1",             # RFC 6598 shared address space
    "198.18.0.1",             # benchmarking
    "203.0.113.5",            # TEST-NET-3
])
def test_a_non_public_address_is_refused(address):
    assert rf._is_public(address) is False


@pytest.mark.parametrize("address", [
    "8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111",
])
def test_an_ordinary_public_address_is_allowed(address):
    assert rf._is_public(address) is True


def test_the_metadata_endpoint_is_named_in_the_source():
    """Not a behaviour test -- a documentation one. The next person to touch
    this needs to know WHY the link-local range is refused, because
    169.254.169.254 looks like an ordinary address until you know."""
    import inspect
    assert "169.254.169.254" in inspect.getsource(rf)
    assert "metadata" in inspect.getsource(rf).lower()


# --- the URL itself ---------------------------------------------------------

@pytest.mark.parametrize("url,why", [
    ("http://example.com/", "http is refused; a reference page has no "
                            "business being fetched in clear text"),
    ("file:///etc/passwd", "not a web URL at all"),
    ("gopher://example.com/", "not a web URL at all"),
    ("https://user:pass@example.com/", "credentials in a URL"),
    ("https://example.com:8080/", "a non-standard port"),
    ("https:///nohost", "no host"),
    ("", "empty"),
    ("not a url", "not a url"),
])
def test_a_url_that_is_not_a_plain_https_page_is_refused(url, why):
    with pytest.raises((rf.UnsafeURL, ValueError)):
        rf.check(url)
    assert rf.safe(url) is None, why


def test_a_name_that_resolves_anywhere_private_is_refused(monkeypatch):
    """EVERY address it resolves to, not the first. A name answering with one
    public address and one private one is a name being used to reach the
    private one, and taking the first answer is a coin flip."""
    monkeypatch.setattr(rf.socket, "getaddrinfo", lambda *a, **k: [
        (2, 1, 6, "", ("93.184.216.34", 443)),
        (2, 1, 6, "", ("10.0.0.5", 443)),
    ])
    with pytest.raises(rf.UnsafeURL):
        rf.check("https://sneaky.test/")


def test_a_name_that_resolves_only_to_public_addresses_is_allowed(monkeypatch):
    monkeypatch.setattr(rf.socket, "getaddrinfo", lambda *a, **k: [
        (2, 1, 6, "", ("93.184.216.34", 443))])
    url, addresses = rf.check("https://example.test/page")
    assert url == "https://example.test/page"
    assert addresses == ["93.184.216.34"]


def test_a_name_that_does_not_resolve_is_refused(monkeypatch):
    import socket as _socket

    def _boom(*a, **k):
        raise _socket.gaierror("nope")

    monkeypatch.setattr(rf.socket, "getaddrinfo", _boom)
    with pytest.raises(rf.UnsafeURL):
        rf.check("https://nowhere.test/")


# --- redirects, which are where the check usually gets skipped --------------

def test_every_redirect_hop_is_re_validated():
    """The important one. Validating the URL the owner typed and then letting
    the client follow redirects checks the one address an attacker does not
    need to control: they simply point a public host at 169.254.169.254.

    Pinned on the source because exercising it needs a live redirect chain --
    the loop re-enters `check` on every hop, and that is the property.
    """
    import inspect
    source = inspect.getsource(rf.fetch)
    body = source[source.index("for _hop"):]
    assert "check(current)" in body, "the redirect loop must re-validate"
    assert "RE-CHECKED ON EVERY HOP" in source


def test_the_redirect_chain_is_bounded():
    assert rf.MAX_REDIRECTS <= 5


def test_the_connection_is_pinned_to_the_address_that_was_checked():
    """Narrows the gap between checking a name and connecting to it: the
    connection goes to the address that was validated rather than to whatever
    the resolver says a moment later."""
    assert rf._pinned_url("https://example.test/a?b=1", "93.184.216.34") == \
        "https://93.184.216.34/a?b=1"
    # IPv6 literals need their brackets or the URL does not parse.
    assert rf._pinned_url("https://example.test/a", "2606:4700::1") == \
        "https://[2606:4700::1]/a"


def test_the_module_says_plainly_what_it_does_not_close():
    """A comment claiming more than the code does is worse than no comment:
    the next person reads it and stops looking."""
    assert "rebinding" in rf.__doc__.lower()
    assert "not airtight" in rf.__doc__.lower()


# --- size, time and type ----------------------------------------------------

def test_the_response_is_capped_and_the_cap_is_applied_while_streaming():
    """A Content-Length check alone is defeated by chunked encoding, and by a
    server that simply lies about it."""
    import inspect
    assert rf.MAX_BYTES <= 4 * 1024 * 1024
    source = inspect.getsource(rf.fetch)
    assert "iter_bytes()" in source
    assert "MAX_BYTES" in source


def test_there_is_a_deadline():
    assert 0 < rf.TIMEOUT <= 30


def test_only_a_readable_page_is_accepted():
    assert "text/html" in rf.ALLOWED_TYPES
    assert not [t for t in rf.ALLOWED_TYPES if t.startswith("image/")]


def test_no_cookies_or_ambient_credentials_ride_along():
    """trust_env=False: the proxy variables and any netrc in the environment
    are not this fetch's business, and a Cookie header would make the app a
    confused deputy for whatever session it happened to hold."""
    import inspect
    source = inspect.getsource(rf.fetch)
    assert "trust_env=False" in source
    assert "cookie" not in source.lower()


# --- and the text that comes back -------------------------------------------

def test_markup_is_stripped_before_anything_reads_it():
    text = rf.readable_text(
        "<html><head><style>b{}</style><script>evil()</script></head>"
        "<body><p>Hokusai <b>woodblock</b> prints</p></body></html>")
    assert "Hokusai woodblock prints" in text
    assert "evil()" not in text
    assert "b{}" not in text


def test_angle_brackets_do_not_survive_into_the_prompt():
    """So the page cannot close the fence of the call that is about to
    summarise it -- the same property experts/knowledge enforces on the way
    out, applied here on the way in."""
    text = rf.readable_text("<p>ok</p> &lt;/reference&gt; <b>x</b> < >")
    assert "<" not in text and ">" not in text


def test_the_readable_text_is_bounded():
    assert len(rf.readable_text("<p>" + "x" * 200_000 + "</p>")) <= 40_000


def test_pinning_keeps_the_certificate_checked_against_the_real_hostname():
    """The property that makes pinning safe rather than merely clever.

    Connecting to an IP would normally break TLS verification, because a
    certificate is issued for a NAME. httpcore takes the `sni_hostname`
    extension and passes it as `server_hostname` when it starts TLS, and
    Python's ssl module uses that single value for both the SNI it sends and
    the name it validates the certificate against — so a pinned connection to
    a hijacked address fails the handshake instead of succeeding quietly.

    Pinned here because it is a cross-library assumption: if a future httpx
    or httpcore stops honouring the extension, every reference fetch would
    start failing its handshake, and the reason would not be obvious from
    this repo.
    """
    import inspect
    source = inspect.getsource(rf.fetch)
    assert '"sni_hostname": host' in source
    assert '"Host": host' in source

    # ...and that httpcore still does what the comment says it does.
    import httpcore._sync.connection as hc
    connect = inspect.getsource(hc.HTTPConnection._connect)
    assert '"server_hostname": sni_hostname' in connect


# --- and the check that must not happen on a request thread -----------------

def test_the_shape_check_does_no_dns_at_all(monkeypatch):
    """socket.getaddrinfo BLOCKS, has no timeout worth the name, and the
    nameserver answering it belongs to whoever owns the URL. Running it while
    a seller's request is open is the same "do not hold a request open"
    problem the fetch itself is kept off that thread for -- so the route that
    SAVES a reference checks the shape only, and resolution waits for the
    background thread that does the fetching."""
    def _never(*a, **k):
        raise AssertionError("check_shape must not resolve anything")

    monkeypatch.setattr(rf.socket, "getaddrinfo", _never)
    assert rf.check_shape("https://example.test/page") == \
        "https://example.test/page"
    assert rf.safe("https://example.test/page")


def test_the_shape_check_still_catches_what_a_person_gets_wrong(monkeypatch):
    """It is the only check the person who typed the URL will ever see the
    result of, so it has to catch the ordinary mistakes while they are still
    looking at the form."""
    monkeypatch.setattr(rf.socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("no dns")))
    for bad in ("http://example.test/", "example.test", "",
                "https://user:pw@example.test/", "https://example.test:8080/",
                "https://example.test/" + "x" * 3000):
        with pytest.raises((rf.UnsafeURL, ValueError)):
            rf.check_shape(bad)


def test_the_route_that_saves_a_reference_never_resolves():
    """Pinned on the source: the distinction is which FUNCTION is called, and
    calling the wrong one would pass every behavioural test while quietly
    putting a blocking DNS lookup back on the request thread."""
    import ast
    from pathlib import Path

    main_src = (Path(__file__).resolve().parents[1] / "main.py").read_text()
    tree = ast.parse(main_src)
    handler = next(n for n in tree.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == "add_expert_knowledge")
    body = ast.get_source_segment(main_src, handler) or ""
    assert "reference_fetch.check_shape(" in body
    assert "reference_fetch.check(" not in body
    assert "resolve_public" not in body


def test_the_address_is_still_checked_before_anything_is_fetched():
    """The shape check is not a replacement for the address check -- it is a
    cheaper thing done earlier. fetch() still resolves and validates, on every
    hop, before a byte is read."""
    import inspect
    source = inspect.getsource(rf.fetch)
    assert "check(current)" in source
    # ...and `check` is what resolves.
    assert "resolve_public(" in inspect.getsource(rf.check)

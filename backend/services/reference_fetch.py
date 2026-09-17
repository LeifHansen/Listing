"""Fetching a page the OWNER asked us to read, without becoming their proxy.

A reference link is how a seller teaches an expert: paste the URL of a
catalogue raisonné, a Levi's dating chart, a Fenton glass mark reference,
write one line about what it is for, and the expert gets smarter without a
deploy.

Which means this app now makes an HTTPS request to an address a USER chose,
from inside the production network, on a box that has cloud credentials
reachable at a well-known link-local address. That is a server-side request
forgery primitive, and it is worth being precise about, because the ways it
goes wrong are not obvious:

  * http://169.254.169.254/ is the cloud metadata endpoint. On most providers
    it hands out instance credentials to anything that asks.
  * http://10.0.0.5/, 127.0.0.1, ::1, fc00::/7 -- the private network the app
    is inside, including every internal service that trusts its own network.
  * ::ffff:10.0.0.1 -- a private IPv4 address wearing an IPv6 costume.
  * A PUBLIC host that redirects to a private one. Checking the URL the user
    typed and then following redirects checks the wrong address.
  * A host that resolves to a public address when validated and a private one
    when connected to, because the answer changed in between (DNS rebinding).
  * A response that never ends, or ends at forty gigabytes.

So: https only, every hop re-validated, every resolved address checked against
the private ranges, a bounded number of redirects, a streaming size cap, a
total deadline, and no cookies or auth headers riding along.

ON THE REBINDING WINDOW, HONESTLY. Validating the resolved address and then
letting httpx resolve it again leaves a gap between the check and the connect.
Closing it properly means pinning the connection to the address that was
validated and carrying the hostname in the Host header, which is what
`_pinned_url` does below for the common case. It is not airtight against an
adversary who controls a DNS server with a one-second TTL and wants it badly
enough; it is a real reduction, the remaining exposure is a GET whose body is
then handed to a summariser and fenced as untrusted, and this comment exists
so nobody reads the module and concludes the hole is closed.

Never used for anything but reading a page the owner named. The page's CONTENT
is untrusted no matter how safely it was fetched -- see experts/knowledge.

--

THREE THINGS THAT ARE NOT "THE PAGE IS UNREADABLE", and used to be recorded as
though they were. All three were reported to the seller as a paywall, and all
three turned the reference off:

  * BUSY IS NOT BROKEN. HTTP 429 and 503 mean "not now". They are retried
    here, with the site's own Retry-After honoured, and anything still failing
    leaves as `TemporaryFailure` so the caller can come back to it rather than
    writing the link off.

  * A WALL IS NOT A PAYWALL. A consent prompt, an ad or newsletter overlay and
    a bot check all arrive as a document with almost no words in it.
    `readable_text` drops the overlay chrome so the page UNDERNEATH is what
    gets read, and `unreadable_reason` names what was actually in the way
    instead of guessing.

  * A CLOSED DOOR IS NOT AN EMPTY ROOM. A site that answers a bare
    "ThryftShop/1.0" with 403 has not told us anything about its page. A
    reference page is fetched once when its owner saves it and again only when
    they press Refresh -- one GET, at human pace, for a page they chose. That
    is a browser's traffic pattern rather than a crawler's, so the reader
    sends a browser's headers, with this app's name and a contact URL kept on
    the end of the User-Agent so an operator reading their logs can see whose
    request it is and who to complain to. REFERENCE_USER_AGENT overrides it.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import time
from typing import Optional
from urllib.parse import urlparse, urlunparse

from ..config import log

MAX_REDIRECTS = 3
MAX_BYTES = 2 * 1024 * 1024        # a reference page, not a download
TIMEOUT = 15.0

# What the reader calls itself. A real browser string with an honest tail: the
# browser part is what gets a page out of sites that refuse unknown clients,
# and the tail is what tells whoever reads the log who we are. Overridable so
# a deployment can be stricter (or politer) without a code change.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
    "ThryftShop/1.0 (+https://thryft.shop)")
USER_AGENT = (os.getenv("REFERENCE_USER_AGENT", "").strip()
              or DEFAULT_USER_AGENT)

# One page, and up to two more goes at it when the site says "not now".
ATTEMPTS = 3
# Statuses that describe the SITE'S MOMENT rather than the page. 429 is the
# one that started this: the Met answers it to anything that looks automated,
# and it used to end up in the settings screen as a dead reference.
RETRY_STATUSES = (408, 425, 429, 500, 502, 503, 504)
# Statuses where the site refused US, which is not a fact about the page.
REFUSED_STATUSES = (401, 402, 403, 407, 451)
BACKOFF = 2.0                      # seconds, doubled per attempt
MAX_PAUSE = 10.0                   # ...and never longer than this, per wait

# What a page of reference material can be. Not a PDF and not an image: the
# distiller reads text, and a content type outside this list is a link that
# was not what the owner thought it was.
ALLOWED_TYPES = ("text/html", "text/plain", "application/xhtml+xml")

_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


# Ranges Python's own flags do not call private but which are not the public
# internet either. 100.64.0.0/10 is the one that matters: it is RFC 6598
# shared address space, several cloud providers route internal traffic over
# it, and `ipaddress` reports it as an ordinary global address.
_EXTRA_DENY = tuple(ipaddress.ip_network(n) for n in (
    "100.64.0.0/10",       # RFC 6598 carrier-grade NAT / shared address space
    "192.0.0.0/24",        # IETF protocol assignments
    "192.0.2.0/24",        # TEST-NET-1
    "198.18.0.0/15",       # network benchmarking
    "198.51.100.0/24",     # TEST-NET-2
    "203.0.113.0/24",      # TEST-NET-3
    "64:ff9b::/96",        # NAT64, which can carry an IPv4 private address
))


class UnsafeURL(ValueError):
    """The URL points somewhere this app must not fetch from."""


class TemporaryFailure(ValueError):
    """The site did not answer with the page THIS TIME.

    Rate limited, briefly down, a connection that dropped. A ValueError like
    the rest so every existing caller still catches it, and its own class so
    the one caller that can do better -- by coming back in a few minutes
    instead of disabling the reference -- can tell the difference.
    """


class Blocked(ValueError):
    """The site refused to serve this reader at all (401/403/451).

    Also not a verdict on the page: nobody has seen it. Kept apart from
    TemporaryFailure because retrying in ninety seconds will not change a 403.
    """


def _is_public(address: str) -> bool:
    """Whether `address` is a routable public address and nothing else."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    # An IPv4 address tunnelled through IPv6 is that IPv4 address, and must
    # be judged as one: ::ffff:169.254.169.254 is the metadata endpoint.
    for attribute in ("ipv4_mapped", "sixtofour"):
        inner = getattr(ip, attribute, None)
        if inner is not None:
            ip = inner
            break

    # Written out one flag per line rather than as one expression. This is the
    # function that decides whether the app will fetch from inside its own
    # network, and it has to be checkable by reading it.
    if ip.is_private:
        return False
    if ip.is_loopback:
        return False
    if ip.is_link_local:        # 169.254.0.0/16 -- the metadata endpoint
        return False
    if ip.is_reserved:
        return False
    if ip.is_multicast:
        return False
    if ip.is_unspecified:       # 0.0.0.0 and ::
        return False
    # IPv6 unique-local (fc00::/7). `is_private` covers it on current
    # CPython, and this is here so it stays covered if that ever changes.
    if getattr(ip, "is_site_local", False):
        return False
    for network in _EXTRA_DENY:
        if ip.version == network.version and ip in network:
            return False
    return True


def resolve_public(host: str) -> list[str]:
    """Every address `host` resolves to, or raise.

    EVERY address, not the first: a name that answers with one public address
    and one private one is a name being used to reach the private one, and
    taking the first answer is a coin flip.
    """
    if not host:
        raise UnsafeURL("no host")
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURL(f"cannot resolve {host}: {exc.__class__.__name__}") from exc
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise UnsafeURL(f"cannot resolve {host}")
    for address in addresses:
        if not _is_public(address):
            raise UnsafeURL(
                f"{host} resolves to a non-public address; a reference link "
                f"must point at the public internet")
    return addresses


def check_shape(url: str) -> str:
    """The URL, if it is the right SHAPE to fetch. No DNS, no network.

    Split from `check` because `socket.getaddrinfo` BLOCKS, has no timeout
    worth the name, and a nameserver an attacker controls can simply not
    answer. The route that saves a reference runs this one -- it is pure
    string work, it catches every mistake a person actually makes (http://, a
    typo, a port, credentials in the URL) while they are still looking at the
    form, and it cannot be used to hold a request open. Resolution happens in
    `check`, on the background thread that does the fetching.
    """
    parts = urlparse((url or "").strip())
    if parts.scheme != "https":
        # https only. A reference page has no business being fetched in
        # clear text, and allowing http doubles the surface for nothing.
        raise UnsafeURL("a reference link must be https")
    if parts.username or parts.password:
        raise UnsafeURL("a reference link must not carry credentials")
    if parts.port not in (None, 443):
        raise UnsafeURL("a reference link must use the standard https port")
    if not parts.hostname:
        raise UnsafeURL("no host")
    if len(url.strip()) > 2000:
        raise UnsafeURL("that address is too long")
    return url.strip()


def check(url: str) -> tuple[str, list[str]]:
    """(the URL, its resolved public addresses) or raise UnsafeURL.

    RESOLVES, so never call it from a request thread -- see check_shape.
    """
    safe_url = check_shape(url)
    return (safe_url, resolve_public(urlparse(safe_url).hostname))


def _pinned_url(url: str, address: str) -> str:
    """`url` with its host replaced by the address that was just validated.

    Closes most of the gap between checking a name and connecting to it: the
    connection goes to the address we checked rather than to whatever the
    resolver says a moment later. The real hostname rides the Host header and
    the TLS SNI, which the caller sets.

    AND THE CERTIFICATE IS STILL CHECKED AGAINST THE REAL HOSTNAME, which is
    the thing that makes this safe rather than merely clever -- pinning to an
    IP would normally break verification, because a certificate is issued for
    a name. httpcore takes the `sni_hostname` extension and passes it as
    `server_hostname` when it starts TLS (httpcore/_sync/connection.py), and
    Python's ssl module uses that one value for BOTH the SNI it sends AND the
    name it validates the certificate against. So a pinned connection to a
    hijacked address fails the handshake rather than succeeding quietly.
    """
    parts = urlparse(url)
    try:
        literal = (f"[{address}]" if isinstance(ipaddress.ip_address(address),
                                                ipaddress.IPv6Address)
                   else address)
    except ValueError:
        return url
    return urlunparse(parts._replace(netloc=literal))


def _reader_headers() -> dict:
    """What a browser sends when a person opens a page.

    Nothing here carries identity -- no Cookie, no Authorization, no Referer
    (see fetch's client, which also refuses the environment's proxy and netrc
    settings). These are the headers whose ABSENCE gets a request refused: a
    client with no Accept-Language and a one-line Accept looks like a scraper
    to every WAF on the internet, and the pages sellers save are exactly the
    kind that sit behind one.

    Accept-Encoding is deliberately NOT set: httpx advertises what it can
    actually decode, and overriding that with a list including brotli means a
    body we cannot read whenever the optional decoder is not installed.
    """
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }


def _retry_after(header: str) -> Optional[float]:
    """`Retry-After` in seconds, if the site sent one we can read.

    Both spellings: a number of seconds, or an HTTP date. The site's own
    answer to "when should I come back" beats any backoff we invent, which is
    the difference between waiting politely and hammering.
    """
    value = str(header or "").strip()
    if not value:
        return None
    try:
        return float(int(value))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except Exception:  # noqa: BLE001 - an unreadable header is simply absent
        return None


def _pause(header: str, attempt: int) -> float:
    """How long to wait before going again. Bounded, always."""
    wait = _retry_after(header)
    if wait is None:
        wait = BACKOFF * (2 ** attempt)
    return max(0.5, min(float(wait), MAX_PAUSE))


def _refusal(status: int) -> ValueError:
    """The status turned into the RIGHT kind of failure.

    Which kind matters more than the wording: it decides whether the caller
    disables the seller's reference or comes back to it.
    """
    if status in RETRY_STATUSES:
        return TemporaryFailure(
            f"the site is busy or limiting how often it will answer "
            f"(HTTP {status})")
    if status in REFUSED_STATUSES:
        return Blocked(f"the site refused to serve the page to us "
                       f"(HTTP {status})")
    return ValueError(f"HTTP {status}")


def fetch(url: str) -> str:
    """The text of the page at `url`, bounded, or raise UnsafeURL/ValueError.

    Returns raw page text. IT IS UNTRUSTED: everything that reads it must
    treat it as data, never as instructions -- see experts/knowledge for the
    fencing, and claude_ai.distill_reference for the summarising pass, which
    deliberately has no tools.

    A status meaning "not now" is tried again, up to ATTEMPTS times, honouring
    the site's own Retry-After. What comes out the other side is a
    TemporaryFailure or a Blocked rather than a bare ValueError, so the caller
    can tell "we have not seen this page" from "this page is no good".
    """
    import httpx

    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        # RE-CHECKED ON EVERY HOP. Validating only the URL the owner typed
        # checks the one address an attacker does not need to control.
        current, addresses = check(current)
        host = urlparse(current).hostname or ""
        moved_to = ""
        for attempt in range(ATTEMPTS):
            pause = 0.0
            try:
                with httpx.Client(timeout=TIMEOUT, follow_redirects=False,
                                  trust_env=False) as client:
                    with client.stream(
                            "GET", _pinned_url(current, addresses[0]),
                            headers={**_reader_headers(), "Host": host},
                            extensions={"sni_hostname": host}) as resp:
                        if resp.status_code in _REDIRECT_STATUSES:
                            location = resp.headers.get("location", "")
                            if not location:
                                raise ValueError("redirect with no location")
                            moved_to = str(httpx.URL(current).join(location))
                        elif (resp.status_code in RETRY_STATUSES
                              and attempt + 1 < ATTEMPTS):
                            # Not now, then. Worked out inside the response so
                            # the site's own Retry-After is what we use, and
                            # slept OUTSIDE it so the connection is not held
                            # open while we wait.
                            pause = _pause(resp.headers.get("retry-after", ""),
                                           attempt)
                        elif resp.status_code != 200:
                            raise _refusal(resp.status_code)
                        else:
                            ctype = (resp.headers.get("content-type") or ""
                                     ).split(";")[0].strip().lower()
                            if ctype and ctype not in ALLOWED_TYPES:
                                raise ValueError(f"not a readable page: {ctype}")
                            # STREAMED, with the cap applied as it arrives. A
                            # Content-Length check alone is defeated by chunked
                            # encoding, and by a server that simply lies.
                            chunks: list[bytes] = []
                            total = 0
                            for chunk in resp.iter_bytes():
                                total += len(chunk)
                                if total > MAX_BYTES:
                                    raise ValueError(
                                        f"the page is larger than "
                                        f"{MAX_BYTES // (1024 * 1024)}MB")
                                chunks.append(chunk)
                            return b"".join(chunks).decode("utf-8", "replace")
            except httpx.HTTPError as exc:
                # A dropped connection or a timed-out handshake is a moment,
                # not a fact about the page -- so it is worth one more go, and
                # what is left is a TemporaryFailure rather than a verdict.
                if attempt + 1 >= ATTEMPTS:
                    raise TemporaryFailure(
                        f"could not read the page "
                        f"({type(exc).__name__})") from exc
                pause = _pause("", attempt)
            if moved_to:
                break
            if pause:
                time.sleep(pause)
        if not moved_to:
            # Unreachable: every path above returns, raises, or sets one of
            # these two. Here so a future edit that breaks that invariant
            # fails loudly instead of returning an empty page.
            raise TemporaryFailure("the site did not answer with a page")
        current = moved_to
    raise ValueError("too many redirects")


# --- what the page actually says --------------------------------------------
#
# Crude on purpose -- no parser dependency, and the output goes to a
# summarising model rather than to a human. But not naive: the pages sellers
# save are ordinary commercial pages, and on those the first two screens of
# markup are a consent prompt, an ad slot and a newsletter overlay. Handing
# that to the summariser and recording its "nothing usable here" as a verdict
# on the PAGE is how a working reference gets reported as a paywall.
#
# So the chrome comes off first, subtree and all, and what is left is read in
# the order a person would: the title and description, then <main>/<article>
# if the page marks one, then the body. A page whose words arrive by
# JavaScript still usually carries them in its JSON-LD, so that is read too
# when the visible text comes up short.

# How few of its own words a page has to come back with before we say it
# was not really seen. Low on purpose: a mark chart or a dating table is a
# short document, and calling one of those a wall is the same mistake in
# the other direction.
_THIN = 250
_EMPTY = 120       # ...and below this it has said nothing at all

_VOID_TAGS = frozenset((
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr"))

# Removed whole. <header> is deliberately NOT here: inside an <article> it
# holds the headline, and the page-level one mostly contains a <nav>, which is.
_CHROME_TAGS = frozenset((
    "script", "style", "noscript", "template", "svg", "head",
    "select", "button", "dialog", "nav", "footer", "aside"))

# What an overlay calls itself. Matched only against an element's id, class,
# role and data-* handles -- never against its text, so a page that discusses
# advertising keeps its words.
_OVERLAY_HINT = re.compile(
    r"(?i)(cookie|consent|gdpr|ccpa|onetrust|didomi|quantcast|usercentrics|"
    r"cookiebot|trustarc|privacy-?(banner|notice|prompt|bar)|"
    r"paywall|regwall|metered?-?wall|subscri\w*-?(wall|modal|overlay|prompt)|"
    r"newsletter|signup-?(modal|overlay|popup)|email-?capture|"
    r"modal|popup|pop-?up|overlay|lightbox|interstitial|takeover|backdrop|"
    r"advert|\bads?[-_]|ad-?(slot|unit|banner|container|wrapper|label)|"
    r"sponsored|promo-?(bar|banner|modal)|notification-?bar|toast|"
    r"skip-?link|breadcrumb|cookie-?law)")

_START_TAG = re.compile(
    r"""<([a-zA-Z][\w:-]*)((?:"[^"]*"|'[^']*'|[^>"'])*?)(/?)>""")
_ATTR = re.compile(r"""([\w:.-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""")


def _attrs(chunk: str) -> dict:
    """An element's attributes, lowercased keys, unquoted values."""
    out = {}
    for match in _ATTR.finditer(chunk or ""):
        value = match.group(2)
        if value[:1] in ('"', "'"):
            value = value[1:-1]
        out[match.group(1).lower()] = value
    return out


def _plain(html: str) -> str:
    """`html` with its markup gone and its whitespace collapsed.

    Angle brackets are stripped entirely on the way OUT as well, so the page
    cannot close the fence of the call that is about to summarise it.
    """
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?s)<[^>]*>", " ", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", " "),
                         ("&gt;", " "), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(entity, char)
    text = text.replace("<", " ").replace(">", " ")
    # Control characters, which are not content and can do odd things in a
    # prompt.
    text = "".join(c for c in text if c == "\n" or c >= " ")
    return " ".join(text.split())


def _subtree_end(html: str, start: int, name: str) -> int:
    """Where the element opened at `start` closes, counting nesting.

    Returns `start` when nothing closes it -- an unbalanced document then
    loses one start tag instead of everything after it, which is the failure
    worth choosing.
    """
    pattern = re.compile(rf"(?is)</?{re.escape(name)}\b[^>]*>")
    depth = 1
    pos = start
    while True:
        match = pattern.search(html, pos)
        if not match:
            return start
        pos = match.end()
        tag = match.group(0)
        if tag.startswith("</"):
            depth -= 1
            if depth <= 0:
                return pos
        elif not tag.endswith("/>"):
            depth += 1


def _is_chrome(name: str, attrs: str) -> bool:
    """Whether this element is furniture rather than the page."""
    if name in _CHROME_TAGS:
        return True
    parsed = _attrs(attrs)
    if parsed.get("role", "").lower() in ("dialog", "alertdialog",
                                          "navigation", "complementary"):
        return True
    if parsed.get("aria-modal", "").lower() == "true":
        return True
    handles = " ".join(parsed.get(key, "") for key in (
        "id", "class", "data-testid", "data-component", "data-module",
        "data-role", "aria-label"))
    return bool(handles.strip() and _OVERLAY_HINT.search(handles))


def _drop_chrome(html: str) -> str:
    """`html` with the furniture removed, subtree and all."""
    out: list[str] = []
    pos, size = 0, len(html or "")
    while pos < size:
        match = _START_TAG.search(html, pos)
        if not match:
            out.append(html[pos:])
            break
        name = match.group(1).lower()
        if (match.group(3) or name in _VOID_TAGS
                or not _is_chrome(name, match.group(2))):
            out.append(html[pos:match.end()])
            pos = match.end()
            continue
        out.append(html[pos:match.start()])
        out.append(" ")
        pos = _subtree_end(html, match.end(), name)
    return "".join(out)


def _main_region(html: str) -> str:
    """The inside of <main>/<article>, when the page marks one and it has
    something in it. What is left over is navigation and boilerplate."""
    for match in _START_TAG.finditer(html or ""):
        name = match.group(1).lower()
        attrs = _attrs(match.group(2))
        if not (name in ("main", "article")
                or attrs.get("role", "").lower() == "main"
                or attrs.get("id", "").lower() in ("main", "content",
                                                   "main-content")):
            continue
        end = _subtree_end(html, match.end(), name)
        if end > match.end():
            inner = html[match.end():end]
            if len(_plain(inner)) >= 200:
                return inner
    return ""


def _json_strings(node, budget: int) -> str:
    """Every human-readable string in a parsed JSON blob, bounded.

    Deliberately not schema-aware: JSON-LD, a museum's embedded record and a
    framework's page data all bury the same facts under different keys, and
    what the summariser needs is the words, not the shape.
    """
    parts: list[str] = []
    seen: set[str] = set()
    stack = [node]
    total = 0
    visits = 0
    while stack and total < budget and visits < 20_000:
        item = stack.pop()
        visits += 1
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
        elif isinstance(item, str):
            value = " ".join(item.split())
            if (2 <= len(value) <= 600 and value not in seen
                    and re.search(r"[A-Za-z]{2}", value)
                    and not value.startswith(("http://", "https://", "//", "/",
                                              "data:", "#", "{", "["))
                    and not re.fullmatch(r"[0-9a-fA-F-]{16,}", value)):
                seen.add(value)
                parts.append(value)
                total += len(value) + 1
    return " ".join(parts)[:budget]


def _structured_text(html: str, budget: int = 8000) -> str:
    """The page's own machine-readable copy of itself, if it ships one.

    A collection record whose text is drawn in the browser still puts its
    catalogue data in a <script type="application/ld+json"> block, and that is
    exactly the material a reference link is saved for.
    """
    import json

    found: list[str] = []
    used = 0
    for match in re.finditer(r"(?is)<script\b([^>]*)>(.*?)</script>",
                             html or ""):
        attrs = _attrs(match.group(1))
        kind = (attrs.get("type") or "").lower()
        if "ld+json" not in kind and not (
                kind == "application/json" and attrs.get("id")):
            continue
        try:
            data = json.loads(match.group(2).strip()[:400_000])
        except Exception:  # noqa: BLE001 - a malformed blob is simply skipped
            continue
        text = _json_strings(data, budget - used)
        if text:
            found.append(text)
            used += len(text) + 1
        if used >= budget:
            break
    return " ".join(found)[:budget]


def _page_lead(html: str) -> str:
    """Title and description: what the page says it is, in its own head.

    Worth reading first and worth reading even when the body comes back thin
    -- on a page that draws itself in the browser this is often the only
    honest sentence in the document.
    """
    bits: list[str] = []
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    if match:
        bits.append(_plain(match.group(1)))
    wanted = ("description", "og:description", "twitter:description",
              "og:title", "og:site_name")
    for match in re.finditer(r"(?is)<meta\b([^>]*)>", html or ""):
        attrs = _attrs(match.group(1))
        key = (attrs.get("name") or attrs.get("property")
               or attrs.get("itemprop") or "").lower()
        if key in wanted:
            value = _plain(attrs.get("content", ""))
            if value and value not in bits:
                bits.append(value)
    return " — ".join(b for b in bits if b)[:600]


def _content_text(html: str) -> str:
    """The page's OWN words, furniture off and no fallbacks.

    Split out because two questions need the same answer: what to hand the
    summariser, and whether the page said anything at all. Measuring the
    second against the text `readable_text` returns would be measuring the
    fallbacks too -- and a consent banner recovered by a fallback is exactly
    the text that must not count as the page having been read.
    """
    stripped = _drop_chrome(html or "")
    body = _plain(_main_region(stripped))
    if len(body) < 200:
        body = _plain(stripped)
    return body


def readable_text(html: str, limit: int = 40_000) -> str:
    """`html` with its tags, scripts, styles and overlays removed, bounded."""
    raw = html or ""
    lead = _page_lead(raw)
    body = _content_text(raw)
    # The stripper is aggressive, and a page that wraps everything it has in
    # something called an overlay would otherwise come back empty and be
    # written off. Falling back to the whole document costs a little noise and
    # saves the reference.
    if len(body) < 200:
        whole = _plain(raw)
        if len(whole) > len(body):
            body = whole
    if len(body) < _THIN:
        extra = _structured_text(raw)
        if extra:
            body = f"{body} {extra}".strip()
    return " ".join(f"{lead} {body}".split())[:limit]


# Ordered: the outermost thing in the way is the one worth naming. A page can
# carry a consent banner AND draw its text in the browser, and if a bot check
# answered instead of the site then neither of those is what happened.
_WALLS = (
    ("a bot check answered instead of the page", (
        "cf-browser-verification", "cf_chl", "cf-challenge", "just a moment",
        "checking your browser", "captcha", "recaptcha", "hcaptcha",
        "attention required", "access denied", "request unsuccessful",
        "incapsula", "pardon our interruption", "unusual traffic",
        "are you a robot", "ddos protection", "perimeterx", "datadome")),
    ("the page asked us to sign in or subscribe first", (
        "paywall", "subscribe to continue", "subscribe to read",
        "log in to continue", "sign in to continue", "members only",
        "create a free account", "register to view", "to continue reading")),
    ("a privacy prompt answered instead of the page", (
        "onetrust", "cookiebot", "didomi", "usercentrics", "quantcast choice",
        "trustarc", "cookie-consent", "cookie consent", "accept all cookies",
        "we use cookies", "your privacy choices")),
    ("the page draws its text in the browser, so there was none to read", (
        "enable javascript", "javascript is required", "javascript to run",
        "__next_data__", "window.__nuxt__", "data-reactroot", "ng-app",
        'id="root"', "id='root'", 'id="app"', "please enable js")),
)


def too_thin(text: str) -> bool:
    """Whether there is too little here to be worth a summarising call.

    The guard in front of the distiller: asking a model to find reference
    material in a consent banner spends a call to be told there is none, and
    then that shrug gets written down as a fact about the seller's page.
    """
    return len(" ".join((text or "").split())) < _EMPTY


def unreadable_reason(html: str, text: str = "") -> str:
    """What stood between us and the page, named, or "" if nothing did.

    Measured on the page's own words rather than on what `readable_text`
    hands back, so the fallbacks -- the whole document, the embedded JSON --
    cannot make a wall look like a page that was read.

    This exists because "a login wall, a paywall, or a page about something
    else" was a guess dressed as a finding, and usually the wrong guess: the
    common cases are a consent prompt, a bot check, and a page that draws its
    own text in the browser. None of those is a paywall, and none of them is a
    reason to turn the seller's reference off.
    """
    content = _content_text(html or "")
    if len(content) >= _THIN:
        return ""
    low = (html or "").lower()
    for reason, markers in _WALLS:
        if any(marker in low for marker in markers):
            return reason
    if len(content) < _EMPTY:
        return ("the site sent an empty page"
                if not (low.strip() or (text or "").strip())
                else "there was almost no text on it")
    return ""


def safe(url: str) -> Optional[str]:
    """`url` if it is the right shape to fetch, else None. No DNS -- this is
    the one for a request thread."""
    try:
        return check_shape(url)
    except (UnsafeURL, ValueError) as exc:
        log.info("reference link refused: %s", exc)
        return None

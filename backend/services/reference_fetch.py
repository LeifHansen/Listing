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
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlparse, urlunparse

from ..config import log

MAX_REDIRECTS = 3
MAX_BYTES = 2 * 1024 * 1024        # a reference page, not a download
TIMEOUT = 15.0
USER_AGENT = "ThryftShop/1.0 (reference link reader; +https://thryft.shop)"

# What a page of reference material can be. Not a PDF and not an image: the
# distiller reads text, and a content type outside this list is a link that
# was not what the owner thought it was.
ALLOWED_TYPES = ("text/html", "text/plain", "application/xhtml+xml")


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


def fetch(url: str) -> str:
    """The text of the page at `url`, bounded, or raise UnsafeURL/ValueError.

    Returns raw page text. IT IS UNTRUSTED: everything that reads it must
    treat it as data, never as instructions -- see experts/knowledge for the
    fencing, and claude_ai.distill_reference for the summarising pass, which
    deliberately has no tools.
    """
    import httpx

    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        # RE-CHECKED ON EVERY HOP. Validating only the URL the owner typed
        # checks the one address an attacker does not need to control.
        current, addresses = check(current)
        host = urlparse(current).hostname or ""
        try:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=False,
                              trust_env=False) as client:
                with client.stream(
                        "GET", _pinned_url(current, addresses[0]),
                        headers={"User-Agent": USER_AGENT,
                                 "Host": host,
                                 "Accept": "text/html,text/plain;q=0.9"},
                        extensions={"sni_hostname": host}) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location", "")
                        if not location:
                            raise ValueError("redirect with no location")
                        current = httpx.URL(current).join(location).__str__()
                        continue
                    if resp.status_code != 200:
                        raise ValueError(f"HTTP {resp.status_code}")
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
            raise ValueError(f"could not read the page "
                             f"({type(exc).__name__})") from exc
    raise ValueError("too many redirects")


def readable_text(html: str, limit: int = 40_000) -> str:
    """`html` with its tags, scripts and styles removed, bounded.

    Crude on purpose -- no parser dependency, and the output goes to a
    summarising model rather than to a human. Angle brackets are stripped
    entirely on the way OUT as well, so the page cannot close the fence of
    the call that is about to summarise it.
    """
    import re
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]*>", " ", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", " "),
                         ("&gt;", " "), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(entity, char)
    text = text.replace("<", " ").replace(">", " ")
    # Control characters, which are not content and can do odd things in a
    # prompt.
    text = "".join(c for c in text if c == "\n" or c >= " ")
    return " ".join(text.split())[:limit]


def safe(url: str) -> Optional[str]:
    """`url` if it is the right shape to fetch, else None. No DNS -- this is
    the one for a request thread."""
    try:
        return check_shape(url)
    except (UnsafeURL, ValueError) as exc:
        log.info("reference link refused: %s", exc)
        return None

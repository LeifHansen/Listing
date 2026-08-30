"""Publishing to Etsy must not fetch whatever URL the browser put on a listing.

`_image_batches` re-uploads a listing's photos to Etsy. Local optimized files
are the normal case, but a listing imported from eBay may only have remote
URLs, so those get fetched server-side and posted to the seller's shop.

That fetch took the URL straight off `listing.image_urls` with no scheme
check, no host check and no size bound — and `image_urls` is not a
server-owned field, so it round-trips through the publish request body like
any other. The request goes out from inside the app, which is the one place
on the network that can reach the metadata service, the database's private
address and anything else bound to localhost; whatever comes back is uploaded
to an Etsy listing the caller owns, so the response is readable too. That is a
server-side request forgery with an exfiltration channel attached.

Nothing legitimate is lost by refusing: `image_urls` is only ever populated
from eBay (ebay_trading imports the EPS picture URLs), and the app already has
exactly the right guard for eBay-hosted photos in image_import.fetch_ebay_image
— HTTPS only, ebayimg.com only, re-checked on every redirect hop, bounded
redirects, bounded size, image content-type. This pins the provider to it.
"""
from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("sqlalchemy")
pytest.importorskip("pydantic")

from backend import storage  # noqa: E402
from backend.marketplaces import etsy_provider  # noqa: E402
from backend.marketplaces.base import PublishContext  # noqa: E402
from backend.models import Listing  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0 pretend jpeg"


class _Resp:
    def __init__(self, content=JPEG, status_code=200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = headers or {"content-type": "image/jpeg"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


@pytest.fixture
def fetched(monkeypatch):
    """Every URL that actually left the process."""
    seen: list[str] = []

    def _get(url, *a, **kw):
        seen.append(url)
        return _Resp()

    monkeypatch.setattr(httpx, "get", _get)
    return seen


def _ctx(session_id: str, **listing_kw) -> PublishContext:
    base = dict(title="Vintage Levi's 501", description="Great pair.",
                price=45.0, quantity=1)
    base.update(listing_kw)
    return PublishContext(session_id=session_id, listing=Listing(**base),
                          mode="live", base_url="https://app.test", uid="u1",
                          prev_record={})


def _batches(ctx):
    return etsy_provider.EtsyProvider()._image_batches(ctx)


def test_an_internal_address_is_never_fetched(fetched):
    """The whole point of the attack: a URL only the server can reach."""
    ctx = _ctx("s-ssrf", image_urls=[
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/"])
    assert _batches(ctx) == []
    assert fetched == [], f"the request went out anyway: {fetched}"


@pytest.mark.parametrize("url", [
    "http://i.ebayimg.com/images/g/abc/s-l1600.jpg",   # plaintext
    "https://evil-ebayimg.com/a.jpg",                  # lookalike host
    "https://ebayimg.com.evil.test/a.jpg",             # suffix trick
    "https://127.0.0.1:8000/api/admin/users",          # loopback
    "https://[::1]/",                                  # loopback, v6
    "file:///etc/passwd",                              # not even http
    "https://metadata.google.internal/computeMetadata/v1/",
])
def test_only_ebays_image_cdn_over_https_is_reachable(fetched, url):
    ctx = _ctx("s-refuse", image_urls=[url])
    assert _batches(ctx) == []
    assert fetched == [], f"{url} was fetched"


def test_a_real_ebay_photo_still_uploads(fetched):
    """The guard must not break the case it exists to serve."""
    url = "https://i.ebayimg.com/images/g/abc/s-l1600.jpg"
    ctx = _ctx("s-ok", image_urls=[url])
    assert _batches(ctx) == [("photo-1.jpg", JPEG)]
    assert fetched == [url]


def test_one_refused_url_does_not_drop_the_rest(fetched):
    """A bad entry is skipped, not fatal — the seller's other photos still go."""
    good = "https://i.ebayimg.com/images/g/xyz/s-l1600.jpg"
    ctx = _ctx("s-mixed", image_urls=["https://169.254.169.254/", good])
    assert _batches(ctx) == [("photo-2.jpg", JPEG)]
    assert fetched == [good]


def test_local_photos_still_win_and_fetch_nothing(fetched):
    """Optimized files are the editable truth; remote URLs are the fallback."""
    opt = storage.optimized_dir("s-local")
    opt.mkdir(parents=True, exist_ok=True)
    (opt / "a.jpg").write_bytes(b"local bytes")
    ctx = _ctx("s-local", images=["a.jpg"],
               image_urls=["https://169.254.169.254/"])
    assert _batches(ctx) == [("a.jpg", b"local bytes")]
    assert fetched == []

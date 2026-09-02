"""`script-src 'unsafe-inline'` made most of the CSP decorative.

The header shipped with `'unsafe-inline'` in script-src, which is the one
allowance that matters: with it, an injected `<script>alert(1)</script>` runs.
`default-src 'self'` still stopped a REMOTE script being fetched, so the
policy was not worthless — but the usual injection does not need a remote
script, and the header's headline promise was not being kept.

It was there for one reason: `index.html` carries a single inline script that
applies the saved theme before first paint, and a policy that blocks the app
is worse than a partial one. The note on `_CSP` said so and said the fix was
its own change. This is it.

The hashes are computed AT STARTUP from the index.html actually on disk, not
written into the source. That is the whole design: a hardcoded hash is a hash
that goes stale the first time someone edits the theme snippet, and the
symptom is a white screen in production, for everyone, after a green deploy.
Reading the served file cannot drift from it.

`'unsafe-inline'` remains the fallback when there is no built frontend to
read — a dev checkout, or a container where the build has not run. There is
nothing to protect there and no policy that could be right.

style-src keeps `'unsafe-inline'` on purpose: React sets element styles
directly and Tailwind emits inline styles, so hashing is not available. That
is a smaller door — CSS injection is a real but much narrower problem than
script execution — and it is documented rather than quietly left open.
"""
from __future__ import annotations

import base64
import hashlib
import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                              re.S | re.I)


def _sha256(body: str) -> str:
    digest = hashlib.sha256(body.encode()).digest()
    return f"'sha256-{base64.b64encode(digest).decode()}'"


@pytest.fixture()
def built():
    """The built index.html, or skip — nothing here is meaningful without it."""
    from backend import main

    path = main.FRONTEND_DIR / "index.html"
    if not path.exists():
        pytest.skip("no built frontend (run `npm run build` in frontend/)")
    return path.read_text()


def test_the_policy_no_longer_allows_arbitrary_inline_script(built):
    """The finding."""
    from backend import main

    script_src = next(p for p in main._CSP.split("; ")
                      if p.startswith("script-src"))
    assert "'unsafe-inline'" not in script_src, script_src
    assert "sha256-" in script_src, script_src


def test_every_inline_script_the_app_ships_is_covered(built):
    """The anti-drift test, and the reason the hashes are computed from the
    file: edit the theme snippet, rebuild, and this stays true. Hardcode a
    hash instead and the first edit ships a white screen."""
    from backend import main

    inline = [m.group(1) for m in INLINE_SCRIPT_RE.finditer(built)]
    assert inline, "index.html has no inline script — has the shape changed?"

    for body in inline:
        assert _sha256(body) in main._CSP, \
            f"an inline script the app serves is not in the policy: {body[:60]!r}"


def test_the_hash_is_of_the_exact_bytes_between_the_tags(tmp_path):
    """A browser hashes the raw text between the tags, whitespace included.
    Hashing a stripped or re-indented body produces a policy that never
    matches, and the symptom is only visible in a browser."""
    from backend import main

    page = tmp_path / "index.html"
    page.write_text("<script>\n  var a = 1;\n</script>")

    csp = main.build_csp(page)
    assert _sha256("\n  var a = 1;\n") in csp
    assert _sha256("var a = 1;") not in csp


def test_nothing_else_was_loosened(built):
    from backend import main

    csp = main._CSP
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'self'" in csp
    # Still allowed, still needed: eBay-hosted listing photos and Google fonts.
    assert "img-src 'self' https: data: blob:" in csp
    assert "https://fonts.googleapis.com" in csp


def test_the_header_a_browser_actually_receives_carries_the_hash(built):
    from backend import main

    csp = TestClient(main.app).get("/api/health").headers["content-security-policy"]
    assert "sha256-" in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp


# ------------------------------------------------- the no-build fallback

def test_without_a_built_frontend_the_policy_does_not_break_the_app(tmp_path):
    """A dev checkout has no dist/. Shipping a hash-only policy there would
    block the one inline script with no hash to permit it, and there is
    nothing to protect: the app cannot serve a frontend at all."""
    from backend import main

    csp = main.build_csp(tmp_path / "does-not-exist.html")
    assert "script-src 'self' 'unsafe-inline'" in csp


def test_a_built_frontend_produces_a_hash_policy(tmp_path):
    page = tmp_path / "index.html"
    page.write_text('<html><script>var a=1;</script>'
                    '<script src="/assets/x.js"></script></html>')

    from backend import main

    csp = main.build_csp(page)
    assert _sha256("var a=1;") in csp
    assert "'unsafe-inline'" not in next(
        p for p in csp.split("; ") if p.startswith("script-src"))


def test_an_external_script_is_not_hashed(tmp_path):
    """Only inline bodies get hashes; `src=` scripts are covered by 'self'.
    Hashing an empty body would add a useless entry to every policy."""
    from backend import main

    page = tmp_path / "index.html"
    page.write_text('<html><script type="module" src="/assets/x.js"></script></html>')

    csp = main.build_csp(page)
    assert _sha256("") not in csp, "an empty body was hashed"
    # And with no inline script to permit, the tightened policy is what ships:
    # 'self' alone is exactly right for a page whose only script has a src.
    script_src = next(p for p in csp.split("; ") if p.startswith("script-src"))
    assert script_src == "script-src 'self'", script_src

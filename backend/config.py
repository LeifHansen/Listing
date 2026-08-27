"""Central configuration loaded from environment variables / .env file."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# --- Logging ---------------------------------------------------------------
# One app-wide logger ("thryft") with a consistent format, independent of
# uvicorn's config so our lines are easy to grep in the Fly logs. Level via
# LOG_LEVEL (default INFO).
log = logging.getLogger("thryft")
if not log.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s thryft: %(message)s"))
    log.addHandler(_handler)
    log.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    log.propagate = False

# --- Paths -----------------------------------------------------------------
# DATA_DIR can be pointed at a mounted volume (e.g. on Fly.io) so uploaded and
# generated files survive restarts; defaults to ./data for local runs.
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT_DIR / "data")))
SESSIONS_DIR = DATA_DIR / "sessions"
EXPORTS_DIR = DATA_DIR / "exports"

for _d in (DATA_DIR, SESSIONS_DIR, EXPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Database (Neon / any Postgres, optional) ------------------------------
# Neon gives a URL like postgresql://user:pass@host/db?sslmode=require


def _env(*names: str) -> str:
    """First real value among the named env vars, in order.

    Placeholder values like '<your production App ID>' (a recurring
    copy-the-template-literally mistake in this app's Fly secrets) are
    treated as unset so features degrade cleanly instead of sending
    garbage to eBay/Postgres.
    """
    for name in names:
        value = os.getenv(name, "").strip()
        if value and "<" not in value:
            return value
    return ""


def _clean_db_url(value: str) -> str:
    """Placeholder-proof like _env, plus require a plausible URL scheme."""
    value = (value or "").strip()
    if not value or "<" in value or not value.startswith(("postgres", "sqlite")):
        return ""
    return value


DATABASE_URL = (_clean_db_url(os.getenv("DATABASE_URL", ""))
                or _clean_db_url(os.getenv("NEON_PRODUCTION_DATABASE_URL", "")))

# --- Auth ------------------------------------------------------------------
# Used to sign session JWTs and the eBay OAuth state. A stable value is
# required so cookies and in-flight OAuth flows survive restarts and work
# across multiple machines. Prefer the env var; otherwise persist a generated
# key under DATA_DIR (a Fly volume) so it stays stable across restarts instead
# of logging every user out and breaking the connect→callback round trip.
def _load_secret_key() -> str:
    env = os.getenv("SECRET_KEY", "").strip()
    if env:
        return env
    key_file = DATA_DIR / ".secret_key"
    try:
        if key_file.is_file():
            saved = key_file.read_text().strip()
            if saved:
                return saved
        generated = os.urandom(32).hex()
        key_file.write_text(generated)
        key_file.chmod(0o600)
        log.warning("SECRET_KEY not set; generated and persisted one at %s. "
                    "Set SECRET_KEY in the environment for a stable value.", key_file)
        return generated
    except OSError:
        # DATA_DIR not writable — fall back to an ephemeral key (sessions won't
        # survive a restart, but the app still runs).
        log.warning("SECRET_KEY not set and %s not writable; using an "
                    "ephemeral key (users will be logged out on restart).", key_file)
        return os.urandom(32).hex()


SECRET_KEY = _load_secret_key()

# Encrypts the marketplace refresh tokens held in the database (see
# backend/crypto.py). A Fernet key -- generate one with:
#   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Left unset, a key is derived from SECRET_KEY, so a self-hosted or local
# deployment is protected with no extra configuration. Set it explicitly if
# SECRET_KEY might ever be rotated: rotating the key a token was written under
# makes that token unreadable, and the seller has to reconnect.
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY", "").strip()

# --- Object storage (Cloudflare R2 / any S3, optional) ---------------------
# Store optimized images in R2 so they survive restarts and are reliably
# fetchable by eBay. Only the three credentials are required: the bucket
# defaults to the canonical name (auto-created on first use), and without a
# public base URL images are served via short-lived presigned URLs instead.
# Requiring all five keys is exactly how R2 sat dormant in production for a
# week while the volume filled — the operator had added the credentials and
# reasonably expected that to be enough.
R2_ACCOUNT_ID = _env("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = _env("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = _env("R2_SECRET_ACCESS_KEY")
R2_BUCKET = _env("R2_BUCKET") or "thryft-images"
# The bucket's public base URL (r2.dev URL or a custom domain), no trailing /.
# Optional: set it to serve photos straight from Cloudflare; unset means the
# app hands out presigned GETs when a local copy is gone.
R2_PUBLIC_BASE_URL = _env("R2_PUBLIC_BASE_URL").rstrip("/")


def r2_missing() -> list[str]:
    """R2 env vars still needed before object storage can run ([] = ready)."""
    required = {
        "R2_ACCOUNT_ID": R2_ACCOUNT_ID,
        "R2_ACCESS_KEY_ID": R2_ACCESS_KEY_ID,
        "R2_SECRET_ACCESS_KEY": R2_SECRET_ACCESS_KEY,
    }
    return [name for name, value in required.items() if not value]


def r2_configured() -> bool:
    return not r2_missing()


def r2_public_urls() -> bool:
    """Serve photos from the bucket's own public URL (vs presigned GETs)."""
    return bool(R2_PUBLIC_BASE_URL)

# --- Native app shell -------------------------------------------------------
# The origin the bundled Capacitor app's pages live on. iOS uses
# capacitor://localhost; Android uses https://localhost (override there).
# Used for CORS and to send OAuth flows back into the app when they finish.
NATIVE_APP_ORIGIN = os.getenv("NATIVE_APP_ORIGIN", "capacitor://localhost").strip().rstrip("/")

# --- Anthropic / Claude ----------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
VISION_MODEL = os.getenv("VISION_MODEL", "claude-opus-4-8").strip()
CONTENT_MODEL = os.getenv("CONTENT_MODEL", "claude-opus-4-8").strip()

# --- Monetization: AI tokens + Stripe --------------------------------------
# The app is free; AI features spend tokens. Every account gets
# FREE_TOKENS_PER_MONTH each calendar month (UTC, no rollover); when they run
# out the user buys a pack (Stripe Checkout) — purchased tokens never expire.
# Billing only activates when TOKENS_ENABLED is set AND a database exists
# (balances are per-account); otherwise every AI feature stays free, which is
# the right default for local dev and self-hosters.
TOKENS_ENABLED = os.getenv("TOKENS_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, "") or default))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    """A float from the environment that can't stop the app from booting.

    Public because services parse their own tunables at module scope. A value
    that isn't a number there ("10m", "600s", a stray quote) raised ValueError
    while the module was still importing, which takes the WHOLE app down at
    boot — not just the one feature the setting belongs to. A typo in a tuning
    knob should cost its default and a log line.
    """
    raw = os.getenv(name, "")
    try:
        return max(0.0, float(raw or default))
    except ValueError:
        log.warning("%s=%r is not a number — using %s", name, raw, default)
        return default


FREE_TOKENS_PER_MONTH = _env_int("FREE_TOKENS_PER_MONTH", 50)

# Stripe (payments). Secret key sk_live_/sk_test_; the webhook signing secret
# (whsec_...) comes from the endpoint you register for checkout.session.completed
# at https://<your-domain>/api/tokens/webhook. Purchases also confirm client-side
# after the Checkout redirect, so the webhook is a safety net, not a requirement.
STRIPE_SECRET_KEY = _env("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = _env("STRIPE_WEBHOOK_SECRET")


def stripe_ready() -> bool:
    return bool(STRIPE_SECRET_KEY)


def tokens_enabled() -> bool:
    """Billing is on: tokens are enforced on AI features."""
    return TOKENS_ENABLED and bool(DATABASE_URL)


def tokens_missing() -> list[str]:
    """What still stands between the current config and a working paid tier
    ([] = ready to take money). Every other integration reports its gaps in
    /api/health; billing did not, so a half-configured launch — metering on
    with no way to pay, or a webhook with no signing secret — looked exactly
    like a working one until a seller hit the wall or a purchase went missing.
    """
    missing = []
    if not TOKENS_ENABLED:
        missing.append("TOKENS_ENABLED")
    if not DATABASE_URL:
        missing.append("DATABASE_URL")
    if not STRIPE_SECRET_KEY:
        missing.append("STRIPE_SECRET_KEY")
    if not STRIPE_WEBHOOK_SECRET:
        # Not fatal on the web (the post-redirect confirm also credits), but
        # it IS the only delivery path for a native purchase, which completes
        # in the system browser where no redirect ever reaches the app.
        missing.append("STRIPE_WEBHOOK_SECRET")
    return missing


def stripe_live_mode() -> Optional[bool]:
    """True on a live key, False on a test key, None when unset/unrecognized.
    Surfaced so "we're taking real money" is never a guess — and so a test key
    left on a production deploy is visible instead of silently accepting
    nothing."""
    if STRIPE_SECRET_KEY.startswith("sk_live_"):
        return True
    if STRIPE_SECRET_KEY.startswith("sk_test_"):
        return False
    return None


if TOKENS_ENABLED and not DATABASE_URL:
    log.warning("TOKENS_ENABLED is set but DATABASE_URL is not — token billing "
                "needs a database for per-account balances, so AI features "
                "remain free until one is configured.")
if TOKENS_ENABLED and DATABASE_URL and not STRIPE_SECRET_KEY:
    log.warning("Token billing is on without STRIPE_SECRET_KEY — users get the "
                "monthly free allowance but cannot buy more tokens.")

# --- Adobe Lightroom / Photoshop (Firefly Services) --------------------------
# A paid photo engine, used only when BG_ENGINE (below) selects it — either
# directly ("adobe") or as Photoroom's backup ("photoroom"). The Lightroom API
# applies our "studio" develop preset, then the Photoshop Remove Background
# service does the cutout, and the result comes back into the listing flow.
# Credentials are a server-to-server OAuth client (id + secret) from a
# developer.adobe.com/console project with the Lightroom + Photoshop APIs
# enabled; both APIs draw on the same Adobe credit pool. NOTE: a single
# "API key" string is not enough — IMS server-to-server auth needs the pair.
#
# Adobe's APIs are async and pull/push files via presigned URLs, so R2 must
# also be configured — it is the hand-off storage.
ADOBE_CLIENT_ID = _env("ADOBE_CLIENT_ID")
ADOBE_CLIENT_SECRET = _env("ADOBE_CLIENT_SECRET")
ADOBE_SCOPES = os.getenv("ADOBE_SCOPES",
                         "openid,AdobeID,firefly_api,ff_apis").strip()
ADOBE_IMS_TOKEN_URL = os.getenv(
    "ADOBE_IMS_TOKEN_URL", "https://ims-na1.adobelogin.com/ims/token/v3").strip()
ADOBE_IMAGE_API_BASE = os.getenv(
    "ADOBE_IMAGE_API_BASE", "https://image.adobe.io").strip().rstrip("/")
# Optional: use your own Lightroom preset (a URL to an exported .xmp) instead
# of the bundled studio look (backend/assets/studio-preset.xmp).
ADOBE_STUDIO_PRESET_URL = os.getenv("ADOBE_STUDIO_PRESET_URL", "").strip()


def adobe_configured() -> bool:
    """Adobe credentials are present (says nothing about the R2 hand-off)."""
    return bool(ADOBE_CLIENT_ID and ADOBE_CLIENT_SECRET)


def adobe_ready() -> bool:
    """The Adobe pipeline can actually run: credentials AND R2 hand-off."""
    return adobe_configured() and r2_configured()


if adobe_configured() and not r2_configured():
    log.warning(
        "Adobe credentials are set but R2 is not configured — the Lightroom/"
        "Photoshop pipeline needs R2 as hand-off storage (Adobe's APIs only "
        "accept presigned URLs). Falling back to the non-Adobe photo pipeline.")

# --- Background removal engines ----------------------------------------------
# BG_ENGINE picks which engine strips photo backgrounds:
#   "pixian"    — Pixian.ai API: the budget engine, roughly a tenth of
#                 Photoroom's per-image price (PIXIAN_API_ID + PIXIAN_API_SECRET)
#   "photoroom" — Photoroom API (PHOTOROOM_API_KEY); Adobe backs it up when
#                 configured. Good quality, but expensive per image.
#   "adobe"     — Adobe Firefly: Lightroom studio preset + Photoshop cutout
#   "local"     — the in-house rembg model on this server (free per photo;
#                 tunables REMBG_MODEL / REMBG_MAX_SIDE / BG_SHADOW are read
#                 in services/images.py)
# Unset (or "auto") means: Pixian when its keys are present, otherwise local.
# The pricey engines NEVER run in auto mode — a configured Photoroom/Adobe key
# alone must not quietly spend money on every photo; set BG_ENGINE to opt in.
#
# Whatever the chain, a failed engine never loses a photo: the original is
# kept and the exact reason (bad key / out of credits / rate limit) is
# surfaced — never a silent mangled cutout.
BG_ENGINE = os.getenv("BG_ENGINE", "auto").strip().lower() or "auto"

PHOTOROOM_API_KEY = _env("PHOTOROOM_API_KEY")

PIXIAN_API_ID = _env("PIXIAN_API_ID")
PIXIAN_API_SECRET = _env("PIXIAN_API_SECRET")
# Pixian's free integration-test mode (results are for testing, not listings).
PIXIAN_TEST = os.getenv("PIXIAN_TEST", "").strip().lower() in ("1", "true", "yes", "on")


def photoroom_ready() -> bool:
    return bool(PHOTOROOM_API_KEY)


def pixian_ready() -> bool:
    return bool(PIXIAN_API_ID and PIXIAN_API_SECRET)


def bg_engine_chain() -> list[str]:
    """Background-removal engines to try, in order. Always non-empty: the
    local model needs no credentials, so it's the floor. An explicit BG_ENGINE
    whose credentials are missing falls back to the auto chain (with a warning
    at import time, below)."""
    if BG_ENGINE == "photoroom" and photoroom_ready():
        return ["photoroom", "adobe"] if adobe_ready() else ["photoroom"]
    if BG_ENGINE == "adobe" and adobe_ready():
        return ["adobe"]
    if BG_ENGINE == "pixian" and pixian_ready():
        return ["pixian"]
    if BG_ENGINE == "local":
        return ["local"]
    return ["pixian", "local"] if pixian_ready() else ["local"]


if BG_ENGINE not in ("auto", "local") and BG_ENGINE not in bg_engine_chain():
    log.warning("BG_ENGINE=%r isn't fully configured (missing credentials?) — "
                "using %s instead.", BG_ENGINE, "/".join(bg_engine_chain()))

# --- eBay ------------------------------------------------------------------
EBAY_ENV = os.getenv("EBAY_ENV", "sandbox").strip().lower()
EBAY_OAUTH_TOKEN = _env("EBAY_OAUTH_TOKEN")
# The Fly secrets store the real production keyset under EBAY_APP_CLIENT_ID /
# EBAY_CERT_ID (eBay's "App ID" and "Cert ID" = OAuth client id and secret),
# while EBAY_CLIENT_ID/SECRET were left as placeholder text — hence the
# fallbacks.
EBAY_CLIENT_ID = _env("EBAY_CLIENT_ID", "EBAY_APP_CLIENT_ID")
EBAY_CLIENT_SECRET = _env("EBAY_CLIENT_SECRET", "EBAY_CERT_ID")
EBAY_REFRESH_TOKEN = _env("EBAY_REFRESH_TOKEN")

EBAY_FULFILLMENT_POLICY_ID = os.getenv("EBAY_FULFILLMENT_POLICY_ID", "").strip()
EBAY_PAYMENT_POLICY_ID = os.getenv("EBAY_PAYMENT_POLICY_ID", "").strip()
EBAY_RETURN_POLICY_ID = os.getenv("EBAY_RETURN_POLICY_ID", "").strip()
EBAY_MERCHANT_LOCATION_KEY = os.getenv("EBAY_MERCHANT_LOCATION_KEY", "").strip()
EBAY_MARKETPLACE_ID = os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US").strip()
EBAY_CURRENCY = os.getenv("EBAY_CURRENCY", "USD").strip()

# "Sign in with eBay": the RuName (redirect URL name) from your eBay app's
# OAuth settings. Required for the connect flow.
EBAY_RUNAME = _env("EBAY_RUNAME")

# Marketplace Account Deletion notifications — eBay requires every Production
# keyset to expose a validated endpoint for these. The verification token is a
# value YOU invent (32-80 chars: letters, digits, underscore, hyphen) and must
# match exactly what you paste into the developer portal's Alerts &
# Notifications page alongside the endpoint URL.
EBAY_VERIFICATION_TOKEN = os.getenv("EBAY_VERIFICATION_TOKEN", "").strip()
# eBay's challenge hash is computed over the endpoint URL exactly as
# registered in the portal. It defaults to the request's own URL, which works
# on Fly (uvicorn runs with --proxy-headers); set it explicitly if the app
# sits behind a proxy that rewrites scheme/host.
EBAY_DELETION_ENDPOINT = os.getenv("EBAY_DELETION_ENDPOINT", "").strip()

# Anything that is not exactly "production" means sandbox. That is a quiet
# footgun: EBAY_ENV=prod (or a typo) silently points the whole integration at
# sandbox, where a seller's real eBay sign-in cannot work and the only symptom
# is a connect that fails with no reason. Say so at boot.
if EBAY_ENV not in ("sandbox", "production"):
    log.warning("EBAY_ENV=%r is not 'sandbox' or 'production' — treating it as "
                "SANDBOX. Real eBay accounts cannot connect against sandbox.",
                EBAY_ENV)
_SANDBOX = EBAY_ENV != "production"
EBAY_API_BASE = "https://api.sandbox.ebay.com" if _SANDBOX else "https://api.ebay.com"
EBAY_AUTH_BASE = "https://auth.sandbox.ebay.com" if _SANDBOX else "https://auth.ebay.com"

# Scopes needed to create listings, read/fetch business policies, run Promoted
# Listings, and read the connected seller's identity (so we can show WHICH eBay
# account is linked). sell.marketing was added later: a seller who connected
# before it must reconnect once to grant it (their existing refresh token only
# carries the scopes they originally approved). Same for sell.fulfillment
# (reading sold orders + posting tracking numbers for the shipping workflow).
# The commit this image was built from, stamped in by the Dockerfile's
# GIT_SHA build arg. Empty for a local run or a build that didn't pass it.
BUILD_SHA = os.getenv("BUILD_SHA", "").strip()

EBAY_OAUTH_SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.marketing",
    "https://api.ebay.com/oauth/api_scope/sell.analytics.readonly",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
]

# eBay label purchasing (Sell Logistics API) is a LIMITED-RELEASE API: eBay
# enables it per application keyset, and requesting its scope from a keyset
# that hasn't been approved fails the whole OAuth consent screen. So the
# sell.logistics scope is opt-in — set EBAY_LOGISTICS_ENABLED once eBay has
# approved the app, and the connect flow starts requesting it (existing
# sellers reconnect once to grant it, same as every scope addition).
EBAY_LOGISTICS_ENABLED = (os.getenv("EBAY_LOGISTICS_ENABLED", "").strip().lower()
                          in ("1", "true", "yes", "on"))
if EBAY_LOGISTICS_ENABLED:
    EBAY_OAUTH_SCOPES.append("https://api.ebay.com/oauth/api_scope/sell.logistics")


# Whether the pre-publish checklist BLOCKS a revise of an already-live listing,
# or only reports what it would have blocked. A relist is always blocked on —
# it creates a new listing, so it answers to the same contract a first publish
# does. A revise is the risky one: these listings are live and selling, some
# were created outside this app, and a checklist that has never run against
# them will find things eBay accepted years ago. So it ships observing first —
# read the "would block" lines out of the logs, confirm they are real, then set
# EBAY_PREFLIGHT_BLOCKS_REVISE=1. Blocking a seller out of editing a live
# listing is worse than the rejection the check is trying to save them from.
EBAY_PREFLIGHT_BLOCKS_REVISE = (
    os.getenv("EBAY_PREFLIGHT_BLOCKS_REVISE", "").strip().lower()
    in ("1", "true", "yes", "on"))


def ebay_oauth_ready() -> bool:
    """Enough config to run the 'Sign in with eBay' flow."""
    return bool(EBAY_CLIENT_ID and EBAY_CLIENT_SECRET and EBAY_RUNAME)


def anthropic_ready() -> bool:
    return bool(ANTHROPIC_API_KEY)


def ebay_status() -> dict:
    """Detailed breakdown of eBay readiness, for surfacing what's missing."""
    has_token = bool(EBAY_OAUTH_TOKEN) or bool(
        EBAY_CLIENT_ID and EBAY_CLIENT_SECRET and EBAY_REFRESH_TOKEN
    )
    checks = {
        "OAuth token": has_token,
        "fulfillment policy": bool(EBAY_FULFILLMENT_POLICY_ID),
        "payment policy": bool(EBAY_PAYMENT_POLICY_ID),
        "return policy": bool(EBAY_RETURN_POLICY_ID),
        "merchant location": bool(EBAY_MERCHANT_LOCATION_KEY),
    }
    missing = [name for name, ok in checks.items() if not ok]
    return {"ready": not missing, "missing": missing, "env": EBAY_ENV}


def ebay_ready() -> bool:
    """True when we have enough config to actually call eBay (not dry-run)."""
    return ebay_status()["ready"]


def taxonomy_ready() -> bool:
    """The Taxonomy API only needs an application token (client id/secret)."""
    return bool(EBAY_CLIENT_ID and EBAY_CLIENT_SECRET)


# --- Etsy ------------------------------------------------------------------
# Etsy Open API v3. OAuth 2.0 authorization-code with PKCE — no client secret
# is ever used, so the only credentials are the app "keystring" and the exact
# redirect URI registered on the Etsy app (https://<host>/api/etsy/callback).
ETSY_CLIENT_ID = _env("ETSY_CLIENT_ID", "ETSY_KEYSTRING")
ETSY_REDIRECT_URI = os.getenv("ETSY_REDIRECT_URI", "").strip()
ETSY_SCOPES = "listings_r listings_w listings_d shops_r shops_w"
ETSY_API_BASE = "https://api.etsy.com/v3"
ETSY_AUTH_URL = "https://www.etsy.com/oauth/connect"
ETSY_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"


def etsy_oauth_ready() -> bool:
    """Enough config to run the 'Sign in with Etsy' flow."""
    return bool(ETSY_CLIENT_ID and ETSY_REDIRECT_URI)


# --- Depop -----------------------------------------------------------------
# Depop's official Selling API is partner-gated (partnerapi.depop.com): the
# endpoints below become known once Depop grants partner credentials, so the
# auth/token URLs are env vars — corrections need zero code changes. The
# integration stays invisible in the UI until all four are set.
DEPOP_CLIENT_ID = _env("DEPOP_CLIENT_ID")
DEPOP_CLIENT_SECRET = _env("DEPOP_CLIENT_SECRET")
DEPOP_API_BASE = os.getenv("DEPOP_API_BASE", "https://partnerapi.depop.com").strip()
DEPOP_AUTH_URL = os.getenv("DEPOP_AUTH_URL", "").strip()
DEPOP_TOKEN_URL = os.getenv("DEPOP_TOKEN_URL", "").strip()
DEPOP_REDIRECT_URI = os.getenv("DEPOP_REDIRECT_URI", "").strip()
DEPOP_SCOPES = os.getenv("DEPOP_SCOPES", "products_read products_write").strip()


def depop_oauth_ready() -> bool:
    """Enough config to run the 'Sign in with Depop' flow (partner creds +
    the OAuth endpoints and redirect URI from the partner setup)."""
    return bool(DEPOP_CLIENT_ID and DEPOP_CLIENT_SECRET
                and DEPOP_AUTH_URL and DEPOP_TOKEN_URL and DEPOP_REDIRECT_URI)

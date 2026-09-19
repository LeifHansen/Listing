"""Central configuration loaded from environment variables / .env file."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# No dependencies and no import-time work of its own, so importing it here
# cannot cycle back through this module. See redact.py's docstring.
from .redact import RedactingFormatter

load_dotenv()

# --- Logging ---------------------------------------------------------------
# One app-wide logger ("thryft") with a consistent format, independent of
# uvicorn's config so our lines are easy to grep in the Fly logs. Level via
# LOG_LEVEL (default INFO).
#
# The formatter redacts. It sits on the HANDLER, so it also covers lines from
# the sub-loggers (thryft.promotions, thryft.metrics) that propagate up here,
# and it scrubs the interpolated result — which is the only thing that works,
# because in `log.warning("...: %s", exc)` the secret is in the args, not the
# format string. See redact.py for why this is a formatter and not a filter.
log = logging.getLogger("thryft")
if not log.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)s thryft: %(message)s"))
    log.addHandler(_handler)
    log.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    log.propagate = False

# --- Error capture ---------------------------------------------------------
# Whether failures are recorded to the error_events table for the Errors tab
# and the daily triage job. Off turns the capture handler into a no-op and
# leaves stdout logging exactly as it was; the app is otherwise unaffected.
ERROR_CAPTURE_ENABLED = os.getenv(
    "ERROR_CAPTURE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
# How long a recorded error is kept. Rows are aggregated per fingerprint, so
# this bounds the number of DISTINCT failures retained, not the traffic —
# pruning runs from the reclaim daemon that already sweeps the volume.
ERROR_TTL_DAYS = int(os.getenv("ERROR_TTL_DAYS", "30") or 30)
# Browser error reports accepted per client per rate-limit window. Generous
# enough for a genuinely broken page, low enough that the unauthenticated
# ingest route cannot be used to fill the table.
CLIENT_ERROR_MAX_PER_WINDOW = int(
    os.getenv("CLIENT_ERROR_MAX_PER_WINDOW", "20") or 20)
# The daily triage job's own door onto the error report. Deliberately NOT
# ADMIN_TOKEN: that one also opens /api/admin/diagnostics, whose payload names
# the Neon host, the database role and the R2 account in raw exception text.
# A robot that only needs to read which bugs are open should not hold a
# credential to that. Unset means CLOSED, like ADMIN_TOKEN.
ERROR_FEED_TOKEN = os.getenv("ERROR_FEED_TOKEN", "").strip()

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


# --- "you set something, just not this" ------------------------------------
# Fly secrets are typed by hand, and a name that is NEARLY right is invisible:
# the app reports the canonical name as missing while the operator is looking
# at a dashboard showing a secret they are certain they set.
#
# Production was in exactly that state once. STRIPE_API_SECRET_KEY and
# STRIPE_API_KEY were both deployed while the code read STRIPE_SECRET_KEY, so
# /api/health reported `tokens_missing: ["STRIPE_SECRET_KEY"]` -- accurate,
# and useless, because the answer looked like "add a Stripe key" when the
# real answer was "rename the one already there". STRIPE_SECRET_KEY now
# accepts STRIPE_API_SECRET_KEY as an alias (below, beside the definition);
# the near-miss list here is for the names that are still NOT read.
#
# This deliberately does NOT alias the near-miss name into use. Adopting it
# would take money-handling configuration from a variable whose contents this
# app never agreed on -- a publishable key sitting in a "secret key" slot would
# read as configured and fail at checkout. Naming what it found and leaving the
# rename to a human is the honest half of the fix.
_NAME_FILLER = frozenset({"API"})


def _name_words(name: str) -> frozenset:
    """A variable name reduced to the words that carry meaning, so that names
    differing only by filler or word order compare equal."""
    return frozenset(w for w in name.upper().split("_") if w and w not in _NAME_FILLER)


def near_miss_env(name: str) -> list[str]:
    """Env vars that ARE set and whose names differ from `name` only by filler
    words or word order. Empty when `name` itself has a value -- there is
    nothing to warn about once the canonical name works."""
    if os.getenv(name, "").strip():
        return []
    want = _name_words(name)
    return sorted(other for other, value in os.environ.items()
                  if other != name and value.strip() and _name_words(other) == want)


def _flag_set_but_false(name: str) -> str:
    """The raw value of an on/off env var that is set to something this app
    does not read as on. '' when it is unset (nothing to explain) or genuinely
    on. TOKENS_ENABLED is deployed in production and still reads as off, which
    `tokens_missing()` can only report as "TOKENS_ENABLED" -- indistinguishable
    from never having set it."""
    raw = os.getenv(name, "").strip()
    if not raw or raw.lower() in ("1", "true", "yes", "on"):
        return ""
    return raw


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


# --- The hostnames this app answers on -------------------------------------
# The app is reachable on more than one origin (app.thryftshop.com and its
# listing-*.fly.dev host). That is invisible to almost everything -- URLs are
# built from the incoming request -- with one exception: an OAuth flow.
#
# A marketplace sends the seller back to the ONE callback URL registered
# against the credential (eBay resolves a RuName to a single accepted URL;
# Etsy and Depop match redirect_uri exactly). The CSRF nonce cookie set when
# the flow starts is host-only, so a connect begun on one origin and returned
# to another arrives with no cookie and is rejected as "expired" -- on a
# hostname where the seller's session cookie does not exist either, so they
# also look logged out.
#
# OAUTH_ORIGIN names the origin those callback URLs point at. When a connect
# starts anywhere else, it is bounced there first (carrying a 60-second ticket,
# since the session cookie cannot cross) and the seller is returned to the
# origin they started on when the flow ends. Leave it unset and none of that
# happens -- the single-origin behaviour every self-hoster and local dev has.
OAUTH_ORIGIN = os.getenv("OAUTH_ORIGIN", "").strip().rstrip("/")

# Every origin this app is served on. The ONLY values a connect flow will send
# a seller back to, which is what keeps the return trip from becoming an open
# redirect: the Host header is client-controlled (Fly forwards what it is
# given), so neither the origin a connect arrives on nor the one asked for in
# the bounce is trusted unless it is named here.
APP_ORIGINS = tuple(
    o.strip().rstrip("/")
    for o in os.getenv("APP_ORIGINS", "").split(",")
    if o.strip()
)


def oauth_return_ok(origin: str) -> bool:
    """Is `origin` one this app is served on, and safe to return a flow to?"""
    return bool(origin) and origin in APP_ORIGINS

# --- Anthropic / Claude ----------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
VISION_MODEL = os.getenv("VISION_MODEL", "claude-opus-4-8").strip()
CONTENT_MODEL = os.getenv("CONTENT_MODEL", "claude-opus-4-8").strip()

# --- Google / Gemini -------------------------------------------------------
# The other vision backend the identifier can run on. The photos go to Gemini
# exactly as they go to Claude; what Gemini adds is GROUNDING — the pricing
# lookup runs against live Google Search results instead of the model's
# memory, which is the half a photo can never supply. Aliased generously
# because Google's own docs and console hand out the same secret under three
# different names.
GOOGLE_API_KEY = _env("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_AI_API_KEY")
# A ROLLING ALIAS by default, not a pinned version. Google retires Gemini
# model ids on its own schedule, and a pinned default is a name that is right
# on the day it is written and a 404 on every draft some months later. The
# alias follows the current build; services/google_ai falls back to whatever
# the key can actually call if even this stops resolving. Pin a specific id
# here when a deployment wants one and will keep it current.
GOOGLE_VISION_MODEL = os.getenv("GOOGLE_VISION_MODEL", "gemini-pro-latest").strip()
# Gemini 2.5 thinks before it answers, and the thinking is billed out of the
# output budget. -1 leaves it to the model (its own dynamic budget); 0 turns
# it off where the model allows that; a positive number caps it in tokens.
GOOGLE_THINKING_BUDGET = os.getenv("GOOGLE_THINKING_BUDGET", "-1").strip()

# Which backend drafts a listing and prices it. "auto" (the default) reads as
# Google when a Google key is set and Anthropic otherwise: a deployment that
# adds the key switches over without a second setting, and one that never
# adds it is untouched. "google" / "anthropic" pin it either way — a pin to a
# backend with no key still falls back rather than taking the app down, and
# says so in config_warnings().
IDENTIFY_PROVIDER = (
    os.getenv("IDENTIFY_PROVIDER", "auto").strip().lower() or "auto")

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
#
# STRIPE_API_SECRET_KEY is accepted as a second name for the same thing, the
# way DATABASE_URL takes NEON_PRODUCTION_DATABASE_URL and ETSY_CLIENT_ID takes
# ETSY_KEYSTRING. That is the name the production keyset was deployed under,
# and reading it here costs nothing, while renaming a live secret restarts the
# machine -- which, with min_machines_running = 1, means restarting it under
# whatever batch is in flight.
#
# The value is still checked rather than trusted: stripe_live_mode() reads the
# sk_live_/sk_test_ prefix, and config_warnings() says so out loud if what
# turns up under either name is not a secret key at all (a publishable pk_ in
# a secret-key slot would otherwise look configured and fail at checkout).
STRIPE_SECRET_KEY = _env("STRIPE_SECRET_KEY", "STRIPE_API_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = _env("STRIPE_WEBHOOK_SECRET", "STRIPE_API_WEBHOOK_SECRET")


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

# Reverse image search for the art lookup: SerpApi's Google Lens engine. Off
# without the key -- the lookup then runs on the model's own recognition plus
# web search. See services/imagesearch.py.
SERPAPI_KEY = _env("SERPAPI_KEY")


def serpapi_ready() -> bool:
    return bool(SERPAPI_KEY)


# --- Background removal: which engine strips photo backgrounds -------------
# BG_ENGINE picks the engine, and the value is the FIRST entry of a chain that
# always ends at "local":
#   "removebg"  — remove.bg's API (REMOVEBG_API_KEY). Owned by Canva. NOTE its
#                 standalone API shuts down 2026-12-01 and migrates to
#                 Leonardo.Ai, which is why leonardo backs it up below.
#   "leonardo"  — Leonardo.Ai (LEONARDO_API_KEY), remove.bg's own named
#                 successor. ~$0.10/image.
#   "local"     — the in-house rembg model on this server (free per photo;
#                 tunables REMBG_MODEL / REMBG_MAX_SIDE are read in
#                 services/images). Always available, so it is the floor.
#   "auto"      — the default: whichever remote engines have credentials, in
#                 the order above, then local.
#
# The local model needs no credentials, so a deploy that sets nothing keeps
# working exactly as it did and spends nothing. A remote engine only runs when
# its key is present.
#
# Whatever the chain, a failed engine never loses a photo: the original is
# kept and the exact reason (bad key / out of credits / rate limit) is
# surfaced — never a silent mangled cutout.
BG_ENGINE = os.getenv("BG_ENGINE", "auto").strip().lower() or "auto"

REMOVEBG_API_KEY = _env("REMOVEBG_API_KEY", "REMOVE_BG_API_KEY")
LEONARDO_API_KEY = _env("LEONARDO_API_KEY", "LEONARDO_AI_API_KEY")


def removebg_ready() -> bool:
    return bool(REMOVEBG_API_KEY)


def leonardo_ready() -> bool:
    return bool(LEONARDO_API_KEY)


def bg_engine_chain() -> list[str]:
    """Background-removal engines to try, in order. Always non-empty: the
    local model needs no credentials, so it's the floor. An explicit BG_ENGINE
    whose credentials are missing falls back to the auto chain (with a warning
    at import time, below)."""
    if BG_ENGINE == "local":
        return ["local"]
    if BG_ENGINE == "removebg" and removebg_ready():
        return (["removebg", "leonardo", "local"] if leonardo_ready()
                else ["removebg", "local"])
    if BG_ENGINE == "leonardo" and leonardo_ready():
        return ["leonardo", "local"]
    chain = []
    if removebg_ready():
        chain.append("removebg")
    if leonardo_ready():
        chain.append("leonardo")
    return chain + ["local"]


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
# The Media API — the one eBay API that does NOT live on api.ebay.com. Videos
# are uploaded through apim.ebay.com/commerce/media/v1_beta, and pointing the
# calls at EBAY_API_BASE above gets a 404 that reads exactly like a bad path.
# It is the same OAuth token and the same sell.inventory scope; only the host
# differs, which is why it is written down here rather than guessed at the
# call site (see services/ebay_video.py).
EBAY_MEDIA_BASE = ("https://apim.sandbox.ebay.com/commerce/media/v1_beta"
                   if _SANDBOX else
                   "https://apim.ebay.com/commerce/media/v1_beta")

# Scopes needed to create listings, read/fetch business policies, run Promoted
# Listings, and read the connected seller's identity (so we can show WHICH eBay
# account is linked). sell.marketing was added later: a seller who connected
# before it must reconnect once to grant it (their existing refresh token only
# carries the scopes they originally approved). Same for sell.fulfillment
# (reading sold orders + posting tracking numbers for the shipping workflow).
# The commit this image was built from, stamped in by the Dockerfile's
# GIT_SHA build arg. Empty for a local run or a build that didn't pass it.
BUILD_SHA = os.getenv("BUILD_SHA", "").strip()

# Gates /api/admin/diagnostics — the deployment detail that used to be served
# anonymously from /api/health (missing env var names, the R2 bucket, free
# disk, Stripe mode, and raw driver exception text carrying the Neon host and
# role or the R2 account id).
#
# Unset means the endpoint is CLOSED, not open. An absent secret has to fail
# closed or a deploy that forgets to set it silently republishes everything
# this was moved to protect.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

EBAY_OAUTH_SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.marketing",
    "https://api.ebay.com/oauth/api_scope/sell.analytics.readonly",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
]

# Buyer messages (Message API) is a LIMITED-RELEASE API: eBay approves the
# commerce.message scope per application keyset, and a keyset that hasn't
# been approved fails the WHOLE consent screen, so nobody could connect eBay
# at all and publishing would stop with it. Hence opt-in — set
# EBAY_MESSAGING_ENABLED once eBay has approved the app.
#
# Flipping it on can't disturb sellers who are already connected:
# refresh_access_token() deliberately omits `scope` (see ebay_auth.py), so
# their tokens keep exactly the scopes they originally granted. Only the
# connect/reconnect flow changes, which makes rollback an env change rather
# than a deploy — existing sellers reconnect once to see their messages.
EBAY_MESSAGING_ENABLED = (os.getenv("EBAY_MESSAGING_ENABLED", "").strip().lower()
                          in ("1", "true", "yes", "on"))
if EBAY_MESSAGING_ENABLED:
    EBAY_OAUTH_SCOPES.append("https://api.ebay.com/oauth/api_scope/commerce.message")


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


def google_ai_ready() -> bool:
    """Enough config to send photos to Gemini."""
    return bool(GOOGLE_API_KEY)


def identify_provider() -> str:
    """Which backend identify() and the pricing lookup actually run on.

    Never returns a backend that has no key: a pin to one that is not
    configured falls back to the one that is, because a seller pressing
    Identify should get a draft, not a 400 about a setting they cannot see.
    """
    if IDENTIFY_PROVIDER == "anthropic" and anthropic_ready():
        return "anthropic"
    if google_ai_ready():
        return "google"
    return "anthropic"


def vision_ready() -> bool:
    """Enough config for SOME vision backend to draft a listing.

    The gate on every route that identifies photos. `anthropic_ready()` is
    still the gate on the passes that are Claude's alone (refine, the aspect
    fills, the art lookup) — those have no Gemini implementation to fall
    back to.
    """
    return anthropic_ready() or google_ai_ready()


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
# Etsy Open API v3. OAuth 2.0 authorization-code with PKCE, so the consent
# handshake itself carries no secret — but every request to Etsy, the token
# exchange included, identifies the APP through the `x-api-key` header, and
# since 2026-02-09 Etsy rejects that header unless it carries the app's shared
# secret beside the keystring (`keystring:secret`; etsy/open-api discussion
# #1529). Both live on the app's page at etsy.com/developers/your-apps. The
# third credential is the exact redirect URI registered on the Etsy app
# (https://<host>/api/etsy/callback).
ETSY_CLIENT_ID = _env("ETSY_CLIENT_ID", "ETSY_KEYSTRING")
ETSY_SHARED_SECRET = _env("ETSY_SHARED_SECRET")
ETSY_REDIRECT_URI = os.getenv("ETSY_REDIRECT_URI", "").strip()
ETSY_SCOPES = "listings_r listings_w listings_d shops_r shops_w"
ETSY_API_BASE = "https://api.etsy.com/v3"
ETSY_AUTH_URL = "https://www.etsy.com/oauth/connect"
ETSY_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"


def etsy_oauth_ready() -> bool:
    """Enough config to run the 'Sign in with Etsy' flow.

    The shared secret is part of "enough": without it the consent screen
    still renders (Etsy checks the keystring and redirect URI there), and the
    token exchange that follows is refused — a Connect button that ends on
    an error page after the seller has said yes. Better to keep the card in
    its "not set up on the server" state, naming the missing variable.
    """
    return bool(ETSY_CLIENT_ID and ETSY_SHARED_SECRET and ETSY_REDIRECT_URI)


def etsy_api_key() -> str:
    """The `x-api-key` header value: `keystring:secret` since 2026-02-09.

    Falls back to the bare keystring only when no secret is configured,
    which etsy_oauth_ready() already refuses to offer to sellers — so the
    fallback serves a developer's dry run, never a live shop.
    """
    if ETSY_CLIENT_ID and ETSY_SHARED_SECRET:
        return f"{ETSY_CLIENT_ID}:{ETSY_SHARED_SECRET}"
    return ETSY_CLIENT_ID


# Etsy app TYPE, which is a separate gate from the credentials above and the
# one that actually stops sellers. Etsy tiers API access in THREE steps, and
# the tier — not the credentials — decides who may authorize this app:
#
#   seller      What Etsy registers by default. Authorizable by the single
#               Etsy account that owns the keystring and nobody else; every
#               other seller is turned away by Etsy itself, on Etsy's page,
#               after leaving this site — "Only the app owner may authorize a
#               seller app". Nothing redirects back, so the app cannot catch
#               it after the fact; the only kind fix is not sending them there.
#   personal    Reviewed and approved by Etsy for a handful of shops (Etsy
#               documents the ceiling as 4). The wall still stands, but the
#               named sellers can now genuinely authorize, so ETSY_OWNER_EMAILS
#               stops being a list of one and becomes a real beta roster.
#   commercial  Unlimited sellers. Granted on an APPROVED personal app, and
#               the end of this gate: nothing left to hold anyone back for.
#
# Unset reads as `seller` — that is what Etsy hands out by default, and it is
# the answer that keeps the gate up. ETSY_COMMERCIAL_ACCESS=true is the older
# way to say `commercial` and still means exactly that.
ETSY_ACCESS_TIERS = ("seller", "personal", "commercial")
# Shops Etsy lets authorize the app, by tier; 0 means no ceiling. Etsy's
# numbers, not ours — ETSY_APP_SEATS overrides them the day Etsy moves them,
# without waiting for a deploy of this file.
_ETSY_TIER_SEATS = {"seller": 1, "personal": 4, "commercial": 0}

ETSY_COMMERCIAL_ACCESS = os.getenv(
    "ETSY_COMMERCIAL_ACCESS", "").strip().lower() in ("1", "true", "yes", "on")
# Kept as typed, and lowered only where it is compared: the warning below has
# to echo the value the operator will be searching their secrets dashboard
# for, not a tidied copy of it.
_ETSY_ACCESS_TIER = os.getenv("ETSY_ACCESS_TIER", "").strip()
_ETSY_APP_SEATS = os.getenv("ETSY_APP_SEATS", "").strip()
ETSY_OWNER_EMAILS = tuple(
    e.strip().lower()
    for e in os.getenv("ETSY_OWNER_EMAILS", "").split(",") if e.strip())


def etsy_access_tier() -> str:
    """Which of Etsy's three access tiers this deployment is on.

    Fails closed twice over: unset and unparseable both read as `seller`, the
    tier that holds back the most, so a typo cannot quietly hand Connect Etsy
    to sellers Etsy is going to refuse. The two are indistinguishable from
    out here, which is why config_warnings() names the typo.
    """
    if ETSY_COMMERCIAL_ACCESS:
        return "commercial"
    if _ETSY_ACCESS_TIER.lower() in ETSY_ACCESS_TIERS:
        return _ETSY_ACCESS_TIER.lower()
    return "seller"


def _etsy_seats_override() -> Optional[int]:
    """ETSY_APP_SEATS as a number, or None when it is unset or unusable.

    try/except rather than a string predicate: `.isdigit()` is true for
    Unicode digits int() then refuses (a superscript "\u00b2" pasted out of a
    rendered doc), and this is read from config_warnings(), which runs at
    module import — so a value that raises here does not cost the seat
    ceiling, it stops the app from booting. env_float above carries the same
    warning about the same trap.

    A negative count is unusable rather than clamped: 0 already means "no
    ceiling" here, so clamping -1 to 0 would turn a typo into the one answer
    that gates nobody.
    """
    try:
        seats = int(_ETSY_APP_SEATS)
    except ValueError:
        return None
    return seats if seats >= 0 else None


def etsy_seat_ceiling() -> int:
    """How many shops may authorize this app at the current tier; 0 = no cap.

    An unreadable ETSY_APP_SEATS falls back to the tier's own number rather
    than to "no cap": the override exists to track Etsy's ceiling, and a typo
    in it must not read as permission to add sellers without one.
    """
    override = _etsy_seats_override()
    return override if override is not None else _ETSY_TIER_SEATS[etsy_access_tier()]


def etsy_gate_active() -> bool:
    """Is the seller-app gate doing anything at all?

    False once Etsy grants Commercial Access (nothing left to gate), and false
    while no owner is named (nothing to gate WITH: there is no way to tell the
    owner from anyone else, and guessing wrong would lock the operator out of
    their own shop). Unconfigured therefore behaves exactly as it did before
    the gate existed — every seller reaches Etsy, and Etsy decides.

    Its own predicate so callers can skip the user lookup behind
    etsy_access_pending(), which is a database round-trip per roster build.
    """
    return bool(ETSY_OWNER_EMAILS) and etsy_access_tier() != "commercial"


def etsy_access_pending(email: Optional[str]) -> bool:
    """Would Etsy's seller-app wall turn THIS user away?"""
    if not etsy_gate_active():
        return False
    return (email or "").strip().lower() not in ETSY_OWNER_EMAILS


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


# --- Which marketplaces this deployment offers -----------------------------
# The launch switch. eBay ships to the mobile app on its own; Etsy and Depop
# are built and stay in the tree — providers registered, mappings intact,
# tests running — but withheld from every seller-facing surface until we come
# back to them.
#
# Withheld is the DEFAULT, not a deployment setting. An unset variable offers
# eBay alone, so a laptop, a CI run, the Fly app and the mobile build all
# agree without anyone remembering to set anything — which is the property
# that commenting the providers out would have given us, without the dead
# code or the seven test files that exist only for these two marketplaces
# and would have had to come out with them.
# Switching one back on is this one variable, no deploy of un-commented code:
#
#   MARKETPLACES_ENABLED=ebay,etsy      # Etsy back, Depop still held
#   MARKETPLACES_ENABLED=all            # everything registered
#
# eBay is not in this gate's gift. The publish and revise paths reach for it
# by name (marketplaces.get("ebay")), so a list that omitted it would not
# launch a leaner app — it would 500 the publish button. It is therefore
# always enabled, and a list that leaves it out is reported rather than
# obeyed: a typo in one env var must not be able to take the product down.
MARKETPLACE_KEYS = ("ebay", "etsy", "depop")
_ALWAYS_ENABLED = ("ebay",)
_MARKETPLACES_ENABLED = os.getenv("MARKETPLACES_ENABLED", "").strip()
_ENABLE_ALL = ("all", "*")


def _named_marketplaces() -> list[str]:
    """The keys the operator actually typed, deduped, lowercased, in order.
    Unknown ones included — config_warnings() is what names them."""
    return list(dict.fromkeys(
        k.strip().lower() for k in _MARKETPLACES_ENABLED.split(",")
        if k.strip()))


def marketplaces_enabled() -> tuple[str, ...]:
    """The marketplace keys this deployment offers, in MARKETPLACE_KEYS order.

    Blank reads as unset, like every other variable in this file, so a
    secret cleared to "" gets the launch default rather than an empty roster.
    """
    if not _MARKETPLACES_ENABLED:
        return _ALWAYS_ENABLED
    if _MARKETPLACES_ENABLED.lower() in _ENABLE_ALL:
        return MARKETPLACE_KEYS
    named = set(_named_marketplaces()) | set(_ALWAYS_ENABLED)
    return tuple(k for k in MARKETPLACE_KEYS if k in named)


def marketplace_enabled(key: str) -> bool:
    """Whether this deployment offers `key`. The registry asks this before
    handing a provider to anything seller-facing.

    Only the marketplaces this app ships (MARKETPLACE_KEYS) can be withheld.
    A key outside that tuple is one the registry knows about and this file
    does not — a provider added without listing it here — and the honest
    answer for it is "enabled": showing up in the roster with its credentials
    missing is a visible bug, whereas vanishing from every surface is a
    silent one. The registry test fails on that mismatch either way.
    """
    key = (key or "").strip().lower()
    if key not in MARKETPLACE_KEYS:
        return True
    return key in marketplaces_enabled()


def _marketplace_warnings() -> list[str]:
    """Two ways this variable disappoints an operator silently."""
    if not _MARKETPLACES_ENABLED or _MARKETPLACES_ENABLED.lower() in _ENABLE_ALL:
        return []
    warnings = []
    named = _named_marketplaces()
    # A misspelled key is the quiet one: the marketplace the operator meant to
    # switch back on stays hidden, and the roster looks exactly as it does
    # when nobody has touched the variable at all.
    unknown = [k for k in named if k not in MARKETPLACE_KEYS]
    if unknown:
        warnings.append(
            f"MARKETPLACES_ENABLED names {', '.join(repr(k) for k in unknown)}, "
            f"which is not one of {'/'.join(MARKETPLACE_KEYS)} — those entries "
            f"are ignored, so a marketplace you meant to switch on is still "
            f"hidden. Enabled: {', '.join(marketplaces_enabled())}.")
    # The other one reads as obedience. eBay is forced on regardless, so an
    # operator who left it out believes they turned it off and every publish
    # still goes to eBay — better to say so than to let the list lie.
    omitted = [k for k in _ALWAYS_ENABLED if k not in named]
    if omitted:
        warnings.append(
            f"MARKETPLACES_ENABLED does not name {', '.join(omitted)}, which "
            f"is always enabled anyway — core publish routes reach for it by "
            f"name. Remove it from the list only by removing those routes.")
    return warnings


# --- Config warnings -------------------------------------------------------
# The credentials an operator types by hand into `fly secrets`. Each is paired
# with the value this module actually resolved, so a name that has a working
# alias (DATABASE_URL / NEON_PRODUCTION_DATABASE_URL) never warns -- only a
# genuinely-unconfigured feature that has a near-miss name sitting next to it.
def _watched_names() -> list[tuple[str, str]]:
    return [
        ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
        ("GOOGLE_API_KEY", GOOGLE_API_KEY),
        ("DATABASE_URL", DATABASE_URL),
        ("SECRET_KEY", SECRET_KEY),
        ("STRIPE_SECRET_KEY", STRIPE_SECRET_KEY),
        ("STRIPE_WEBHOOK_SECRET", STRIPE_WEBHOOK_SECRET),
        ("SERPAPI_KEY", SERPAPI_KEY),
        ("REMOVEBG_API_KEY", REMOVEBG_API_KEY),
        ("LEONARDO_API_KEY", LEONARDO_API_KEY),
        ("EBAY_VERIFICATION_TOKEN", EBAY_VERIFICATION_TOKEN),
        ("R2_ACCOUNT_ID", R2_ACCOUNT_ID),
        ("R2_ACCESS_KEY_ID", R2_ACCESS_KEY_ID),
        ("R2_SECRET_ACCESS_KEY", R2_SECRET_ACCESS_KEY),
        ("ETSY_REDIRECT_URI", ETSY_REDIRECT_URI),
        ("ETSY_SHARED_SECRET", ETSY_SHARED_SECRET),
    ]


def config_warnings() -> list[str]:
    """Misconfigurations that look identical to "not configured yet".

    Everything here is a case where the operator DID act and the app still
    reports the feature as missing, so the honest message is not "set this"
    but "you set something adjacent". Reported by /api/health next to the
    `*_missing` lists those cases would otherwise hide behind.
    """
    warnings = []
    for name, resolved in _watched_names():
        for other in near_miss_env(name):
            if not resolved:
                warnings.append(
                    f"{other} is set but this app reads {name} — rename the "
                    f"secret (or add {name}) or the feature stays off.")
    # A pinned identifier backend with no key. It fails OPEN (the other
    # backend drafts the listing), so nothing breaks and nothing says so —
    # the operator believes every draft is coming from Gemini while every
    # draft is still coming from Claude.
    if IDENTIFY_PROVIDER == "google" and not GOOGLE_API_KEY:
        warnings.append(
            "IDENTIFY_PROVIDER=google but GOOGLE_API_KEY is not set, so "
            "listings are still drafted by Claude — set the key or the pin "
            "does nothing.")
    elif IDENTIFY_PROVIDER == "anthropic" and not ANTHROPIC_API_KEY:
        warnings.append(
            "IDENTIFY_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set, "
            "so listings are drafted by whatever backend is configured.")
    elif IDENTIFY_PROVIDER not in ("auto", "google", "anthropic"):
        warnings.append(
            f"IDENTIFY_PROVIDER={IDENTIFY_PROVIDER!r} is not one of "
            f"auto/google/anthropic — it is being read as 'auto'.")
    stray = _flag_set_but_false("TOKENS_ENABLED")
    if stray:
        warnings.append(
            f"TOKENS_ENABLED={stray!r} is not one of 1/true/yes/on, so token "
            f"billing is OFF — which reads the same as never setting it.")
    # A keystring without its shared secret is the newest way Etsy looks
    # unconfigured: the credentials that worked before 2026-02-09 now fail
    # every request, and nothing in the OAuth dance says so until the token
    # exchange. Named here because the operator who set two Etsy variables
    # will not think to look for a third.
    if ETSY_CLIENT_ID and not ETSY_SHARED_SECRET:
        warnings.append(
            "ETSY_CLIENT_ID is set but ETSY_SHARED_SECRET is not — since "
            "2026-02-09 Etsy rejects every request whose x-api-key carries "
            "the keystring alone, so Etsy stays off until the shared secret "
            "(from the app's page at etsy.com/developers/your-apps) is set.")
    # Same trap, and it fails closed: an unparsed value reads as "Commercial
    # Access not granted", so the operator thinks they opened Etsy to every
    # seller while the app is still quietly showing them a pending-review card.
    stray = _flag_set_but_false("ETSY_COMMERCIAL_ACCESS")
    if stray:
        warnings.append(
            f"ETSY_COMMERCIAL_ACCESS={stray!r} is not one of 1/true/yes/on, so "
            f"Etsy is still gated to ETSY_OWNER_EMAILS — which reads the same "
            f"as never setting it.")
    # The tier fails closed the same way, and its typo is the quieter one: a
    # misspelled tier reads as `seller`, so the operator believes their
    # approved app is seating a beta while every named seller but one is
    # still being held back by this app.
    if _ETSY_ACCESS_TIER and _ETSY_ACCESS_TIER.lower() not in ETSY_ACCESS_TIERS:
        warnings.append(
            f"ETSY_ACCESS_TIER={_ETSY_ACCESS_TIER!r} is not one of "
            f"{'/'.join(ETSY_ACCESS_TIERS)}, so Etsy is treated as an "
            f"unapproved seller app — which reads the same as never setting "
            f"it.")
    if _ETSY_APP_SEATS and _etsy_seats_override() is None:
        warnings.append(
            f"ETSY_APP_SEATS={_ETSY_APP_SEATS!r} is not a seat count (a whole "
            f"number, 0 for no ceiling), so the ceiling for the "
            f"{etsy_access_tier()} tier is used instead.")
    # And the one that puts sellers back in front of Etsy's error page. Naming
    # more sellers than Etsy seats does not seat them: it waves the overflow
    # past THIS app's gate, and Etsy refuses them on its own page, off-site,
    # with nothing redirected back — the exact dead end the gate exists to
    # prevent. Nobody finds that out from the roster, because from in here a
    # named seller and a seated one look identical.
    # Keyed on the gate being up, not just on the list being long: at
    # commercial tier the roster gates nobody, so a stale list left behind
    # there is untidy rather than harmful — and warning about it would train
    # the operator to ignore the line that means something.
    seats = etsy_seat_ceiling()
    if etsy_gate_active() and seats and len(ETSY_OWNER_EMAILS) > seats:
        tier, over = etsy_access_tier(), len(ETSY_OWNER_EMAILS) - seats
        detail = ("a seller app is authorizable by the keystring's owner "
                  "alone" if tier == "seller" else
                  f"Etsy's {tier} tier seats {seats}")
        warnings.append(
            f"ETSY_OWNER_EMAILS names {len(ETSY_OWNER_EMAILS)} sellers and "
            f"{detail}, so {over} of them skip this app's pending card and "
            f"are refused on Etsy's own page instead — the dead end the gate "
            f"exists to prevent. Trim the list, or set ETSY_APP_SEATS if Etsy "
            f"has moved the ceiling.")
    # A Stripe key that is present but is not a SECRET key. Worth its own line
    # now that the secret is read from either of two names: a publishable
    # pk_... sitting in the slot satisfies every "is it configured?" check in
    # the app and then fails at the one moment that matters, when a seller
    # tries to buy tokens.
    if STRIPE_SECRET_KEY and stripe_live_mode() is None:
        warnings.append(
            "The Stripe secret key doesn't start with sk_live_ or sk_test_, so "
            "it isn't a secret key — checkout will fail even though every "
            "readiness check passes. (A publishable pk_... key belongs in "
            "STRIPE_PUBLISHABLE_KEY.)")
    warnings += _marketplace_warnings()
    return warnings


for _w in config_warnings():
    log.warning(_w)

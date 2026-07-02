"""Central configuration loaded from environment variables / .env file."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# --- Auth ------------------------------------------------------------------
# Used to sign session JWTs. Set a stable value in production so sessions
# survive restarts; otherwise a random per-process key is generated.
SECRET_KEY = os.getenv("SECRET_KEY", "").strip() or os.urandom(32).hex()

# --- Anthropic / Claude ----------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
VISION_MODEL = os.getenv("VISION_MODEL", "claude-opus-4-8").strip()
CONTENT_MODEL = os.getenv("CONTENT_MODEL", "claude-opus-4-8").strip()

# --- eBay ------------------------------------------------------------------
EBAY_ENV = os.getenv("EBAY_ENV", "sandbox").strip().lower()
EBAY_OAUTH_TOKEN = os.getenv("EBAY_OAUTH_TOKEN", "").strip()
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "").strip()
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "").strip()
EBAY_REFRESH_TOKEN = os.getenv("EBAY_REFRESH_TOKEN", "").strip()

EBAY_FULFILLMENT_POLICY_ID = os.getenv("EBAY_FULFILLMENT_POLICY_ID", "").strip()
EBAY_PAYMENT_POLICY_ID = os.getenv("EBAY_PAYMENT_POLICY_ID", "").strip()
EBAY_RETURN_POLICY_ID = os.getenv("EBAY_RETURN_POLICY_ID", "").strip()
EBAY_MERCHANT_LOCATION_KEY = os.getenv("EBAY_MERCHANT_LOCATION_KEY", "").strip()
EBAY_MARKETPLACE_ID = os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US").strip()
EBAY_CURRENCY = os.getenv("EBAY_CURRENCY", "USD").strip()

# "Sign in with eBay": the RuName (redirect URL name) from your eBay app's
# OAuth settings. Required for the connect flow.
EBAY_RUNAME = os.getenv("EBAY_RUNAME", "").strip()

_SANDBOX = EBAY_ENV != "production"
EBAY_API_BASE = "https://api.sandbox.ebay.com" if _SANDBOX else "https://api.ebay.com"
EBAY_AUTH_BASE = "https://auth.sandbox.ebay.com" if _SANDBOX else "https://auth.ebay.com"

# Scopes needed to create listings and read/fetch business policies.
EBAY_OAUTH_SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
]


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

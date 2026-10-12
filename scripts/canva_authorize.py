#!/usr/bin/env python3
"""Connect the app's Canva account for background removal, once.

    python3 scripts/canva_authorize.py
    python3 scripts/canva_authorize.py --redirect https://app.example.com/oauth/canva

Canva's REST API has no API keys: every call acts as a Canva USER, with a
bearer token from the OAuth 2.0 authorization-code flow (with PKCE). This
script walks that flow on an operator's laptop and prints the three values
the server needs -- CANVA_CLIENT_ID, CANVA_CLIENT_SECRET and
CANVA_REFRESH_TOKEN -- as a `fly secrets set` line. After that the server
refreshes the token itself (backend/services/cutout_api.py), and this script
is only needed again if that connection is revoked or its token spent.

BEFORE RUNNING IT, in Canva's developer portal (https://www.canva.com/developers/apps):

  1. Create an app (or open the one this deploy uses). Under Outside Canva ->
     Configuration, turn on "Canva REST APIs" under Integration methods.
  2. Under Scopes, grant: asset (read AND write), design:content (read AND
     write), profile (read).
  3. Turn on the IMAGE TRANSFORMATION preview API for the app -- without it
     every cutout fails with "Image transformation is not enabled".
  4. Under Redirect URLs, add the URL you will pass as --redirect (default
     http://127.0.0.1:3001/oauth/redirect). It need not be served by anything:
     the browser lands on it with ?code=..., and you paste that address here.
  5. Copy the Client ID and generate a Client secret.

The Canva account that authorizes must be on a plan with the background
remover and transparent PNG export (Canva Pro or above); the script checks
both capabilities before it prints anything, so a Free account is told so
here rather than on the first seller's photo.

WHY PASTE A URL INSTEAD OF LISTENING ON A PORT. It works the same on a laptop,
over SSH, and for a redirect URL on a real domain; a local listener only works
for the first of those. Nothing secret is in the pasted address but the
single-use code, which this script spends immediately.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import os
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlparse

AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
TOKEN_URL = "https://api.canva.com/auth/v1/oauth/token"
API = "https://api.canva.com/rest/v1"
# What one cutout needs: asset read/write (upload, transform, delete),
# design:content read/write (the design around the result, and its export),
# profile:read (the capability check below).
SCOPES = ["asset:read", "asset:write", "design:content:read",
          "design:content:write", "profile:read"]
NEEDED_CAPABILITIES = {"background_removal", "export_png_transparency"}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--redirect", default="http://127.0.0.1:3001/oauth/redirect",
                        help="a redirect URL registered on the Canva app")
    parser.add_argument("--client-id", default=os.getenv("CANVA_CLIENT_ID", ""))
    args = parser.parse_args()

    try:
        import httpx
    except ImportError:
        print("This script needs httpx: pip install httpx", file=sys.stderr)
        return 2

    client_id = args.client_id or input("Canva Client ID: ").strip()
    client_secret = (os.getenv("CANVA_CLIENT_SECRET", "").strip()
                     or getpass.getpass("Canva Client secret (not echoed): ").strip())
    if not client_id or not client_secret:
        print("Both the client ID and the client secret are needed.", file=sys.stderr)
        return 2

    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(24)
    url = AUTHORIZE_URL + "?" + urlencode({
        "code_challenge": challenge,
        "code_challenge_method": "s256",
        "scope": " ".join(SCOPES),
        "response_type": "code",
        "client_id": client_id,
        "state": state,
        "redirect_uri": args.redirect,
    })
    print("\n1. Open this in a browser signed in to the Canva account the app "
          "should use, and click Allow:\n\n   " + url + "\n")
    print("2. The browser lands on your redirect URL (the page may not load — "
          "that is fine). Paste that whole address here.\n")
    landed = input("Redirected to: ").strip()
    query = parse_qs(urlparse(landed).query)
    if query.get("error"):
        print(f"Canva refused: {query['error'][0]} "
              f"{query.get('error_description', [''])[0]}", file=sys.stderr)
        return 1
    code = (query.get("code") or [""])[0]
    if not code:
        print("That address carries no ?code= — paste the one the browser "
              "landed on after Allow.", file=sys.stderr)
        return 1
    if (query.get("state") or [""])[0] != state:
        print("The state in that address is not the one this run sent; "
              "start over rather than exchange it.", file=sys.stderr)
        return 1

    resp = httpx.post(TOKEN_URL, auth=(client_id, client_secret), data={
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": args.redirect,
    }, timeout=30)
    if resp.status_code != 200:
        print(f"Token exchange failed (HTTP {resp.status_code}): "
              f"{resp.text[:300]}", file=sys.stderr)
        return 1
    tokens = resp.json()
    access, refresh = tokens["access_token"], tokens["refresh_token"]

    caps = httpx.get(f"{API}/users/me/capabilities",
                     headers={"Authorization": f"Bearer {access}"}, timeout=30)
    have = set()
    if caps.status_code == 200:
        have = set(caps.json().get("capabilities") or [])
    missing = NEEDED_CAPABILITIES - have
    print("\nConnected.")
    if caps.status_code != 200:
        print(f"   (couldn't read the account's capabilities: HTTP "
              f"{caps.status_code} — check the plan by hand)")
    elif missing:
        print(f"   WARNING: this Canva account lacks {sorted(missing)}. "
              "Background removal and transparent PNG export need a paid "
              "plan (Canva Pro); cutouts will fail until it has one.")
    else:
        print("   The account can remove backgrounds and export transparent "
              "PNGs.")

    print("\n3. Set these on the server (the refresh token is single-use "
          "and rotates: the server keeps the newest one on its volume, this "
          "one is the seed):\n")
    print(f"   fly secrets set CANVA_CLIENT_ID='{client_id}' "
          f"CANVA_CLIENT_SECRET='{client_secret}' "
          f"CANVA_REFRESH_TOKEN='{refresh}'\n")
    print("   Then /api/ready should list \"canva\" ahead of \"local\" under "
          "image_engine.chain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

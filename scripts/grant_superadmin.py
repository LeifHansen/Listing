"""Grant (or revoke) the operator console for one account, by email.

    python3 scripts/grant_superadmin.py seller@example.com
    python3 scripts/grant_superadmin.py seller@example.com --revoke

This script is the ONLY way a superadmin is made. Deliberately not an env
var read at boot (a SUPERADMIN_EMAILS list re-asserts on every start, so a
typo'd address becomes a standing grant to whoever signs up with it — a
fail-open shape this codebase refuses everywhere else), and deliberately not
a route (the console must not be able to mint more of itself). On Fly:
`fly ssh console`, then run it there.

Even this bootstrap lands in the audit trail: the grant writes an
admin_audit_log row with actor "ops", so "who made this account an admin,
and when" always has an answer.

Reads DATABASE_URL from the environment via backend.config. Refuses an
unknown email loudly rather than creating anything.
"""
from __future__ import annotations

import argparse
import getpass
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Grant or revoke the superadmin role for one account.")
    parser.add_argument("email", help="the account's email address")
    parser.add_argument("--revoke", action="store_true",
                        help="set the role back to 'user'")
    args = parser.parse_args()

    if not db.enabled():
        print("No DATABASE_URL configured — there are no accounts here.")
        return 1

    email = args.email.strip().lower()
    rec = db.get_user_by_email(email)
    if not rec:
        print(f"No account for {email!r}. Nothing was changed.")
        return 1

    role = "user" if args.revoke else "superadmin"
    before = rec.get("role") or "user"
    if before == role:
        print(f"{email} already has role {role!r}. Nothing to do.")
        return 0

    operator = f"{getpass.getuser()}@{socket.gethostname()}"
    db.admin_audit(
        {"id": "ops", "email": operator},
        "revoke_superadmin" if args.revoke else "grant_superadmin",
        target_type="user", target_id=rec["id"],
        data={"email": email})
    updated = db.set_user_role(rec["id"], role)
    if not updated:
        print("The account vanished mid-update. Nothing was changed.")
        return 1
    print(f"{email}: role {before!r} -> {updated['role']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Transactional email via SendGrid (v3 mail/send, plain httpx — no SDK).

Currently one automation: the onboarding nudge — users who signed up
NUDGE_AFTER_DAYS ago and still have zero listings get a friendly "your first
flip is three photos away" email, exactly once. A daemon thread runs a pass
shortly after startup and then daily; the nudge_sent_at marker in the users
table makes passes idempotent, so restarts and overlapping deploys are safe.

Requires (Fly secrets / .env):
  SENDGRID_API_KEY  - SendGrid API key with Mail Send permission
  EMAIL_FROM        - a SendGrid-verified sender, e.g. QuickFlip <hi@domain.com>
Without both set, the automation stays off (one log line says so).
"""
from __future__ import annotations

import threading
import time

import httpx

from .. import config, db
from ..config import log

SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"
# First pass a minute after boot (let the app settle), then daily.
FIRST_PASS_DELAY_S = 60
PASS_INTERVAL_S = 24 * 60 * 60


def _parse_from(raw: str) -> dict:
    """'QuickFlip <hi@x.com>' -> {email, name}; bare addresses work too."""
    raw = raw.strip()
    if "<" in raw and raw.endswith(">"):
        name, _, rest = raw.partition("<")
        return {"email": rest[:-1].strip(), "name": name.strip() or "QuickFlip"}
    return {"email": raw, "name": "QuickFlip"}


def send(to_email: str, subject: str, html: str, text: str) -> bool:
    """Send one email; True on success. Failures are logged, never raised."""
    if not config.email_ready():
        return False
    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": _parse_from(config.EMAIL_FROM),
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text},
            {"type": "text/html", "value": html},
        ],
    }
    try:
        resp = httpx.post(
            SENDGRID_URL,
            headers={"Authorization": f"Bearer {config.SENDGRID_API_KEY}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        if resp.status_code == 202:
            return True
        log.warning("email: sendgrid %s for %s: %s",
                    resp.status_code, to_email, resp.text[:300])
        return False
    except httpx.HTTPError as exc:
        log.warning("email: send to %s failed: %s", to_email, exc)
        return False


# --------------------------------------------------------------------------
# Onboarding nudge template — brand voice: friendly, light, zero pressure.
# Inline styles only (email clients ignore stylesheets).
# --------------------------------------------------------------------------

def nudge_email(display_name: str) -> tuple[str, str, str]:
    """(subject, html, text) for the 'no listings yet' nudge."""
    name = (display_name or "").strip()
    hey = f"Hey {name}," if name else "Hey,"
    url = config.APP_PUBLIC_URL
    subject = "Your first flip is 3 photos away 📦"
    text = (
        f"{hey}\n\n"
        "You signed up for QuickFlip a few days ago, but your shelf is still "
        "empty — let's fix that in about two minutes:\n\n"
        "  1. Snap a few photos of anything you want to sell\n"
        "  2. The AI writes the title, description, and price\n"
        "  3. Review and publish straight to eBay\n\n"
        f"Start your first listing: {url}\n\n"
        "Out hunting instead? Shop Mode tells you what an item resells for "
        "before you buy it.\n\n"
        "Happy flipping!\n— QuickFlip\n\n"
        "You're getting this one-time tip because you created a QuickFlip "
        "account. No more emails unless you ask for them."
    )
    html = f"""\
<div style="margin:0;padding:32px 16px;background:#fafafb;font-family:Inter,-apple-system,'Segoe UI',sans-serif;color:#1f2937;">
  <div style="max-width:520px;margin:0 auto;background:#ffffff;border:1px solid #ececec;border-radius:22px;padding:32px;">
    <div style="font-size:20px;font-weight:800;margin-bottom:20px;">
      Quick<span style="color:#0064d2;">Flip</span> <span style="font-size:22px;">⚡</span>
    </div>
    <h1 style="font-size:22px;font-weight:700;margin:0 0 12px;">{hey} your shelf looks a little empty 📦</h1>
    <p style="font-size:15px;line-height:1.6;color:#6b7280;margin:0 0 20px;">
      You signed up a few days ago but haven't listed anything yet.
      Your first flip takes about two minutes:
    </p>
    <table role="presentation" style="border-collapse:collapse;margin:0 0 24px;">
      <tr><td style="padding:6px 12px 6px 0;font-size:15px;">📷</td>
          <td style="padding:6px 0;font-size:15px;">Snap a few photos of anything you want to sell</td></tr>
      <tr><td style="padding:6px 12px 6px 0;font-size:15px;">✨</td>
          <td style="padding:6px 0;font-size:15px;">The AI writes the title, description, and price</td></tr>
      <tr><td style="padding:6px 12px 6px 0;font-size:15px;">🚀</td>
          <td style="padding:6px 0;font-size:15px;">Review and publish straight to eBay</td></tr>
    </table>
    <a href="{url}" style="display:inline-block;background:#0064d2;color:#ffffff;text-decoration:none;font-weight:600;font-size:15px;padding:13px 28px;border-radius:999px;">
      Create your first listing
    </a>
    <p style="font-size:13px;line-height:1.6;color:#6b7280;margin:24px 0 0;">
      Out hunting instead? <strong style="color:#1f2937;">Shop Mode</strong> tells you
      what an item resells for before you buy it.
    </p>
    <hr style="border:none;border-top:1px solid #ececec;margin:24px 0;" />
    <p style="font-size:12px;color:#9ca3af;margin:0;">
      You're getting this one-time tip because you created a QuickFlip account.
      No more emails unless you ask for them.
    </p>
  </div>
</div>"""
    return subject, html, text


def run_nudge_pass() -> dict:
    """Email everyone who qualifies right now. Returns counts for logging."""
    users = db.users_needing_nudge(config.NUDGE_AFTER_DAYS)
    sent = failed = 0
    for u in users:
        subject, html, text = nudge_email(u.get("display_name", ""))
        if send(u["email"], subject, html, text):
            db.mark_nudge_sent(u["id"])
            sent += 1
        else:
            failed += 1  # stays unmarked; the next daily pass retries
    if users:
        log.info("email: nudge pass — %d candidates, %d sent, %d failed",
                 len(users), sent, failed)
    return {"candidates": len(users), "sent": sent, "failed": failed}


def start_scheduler() -> None:
    """Daily nudge pass in a daemon thread (the app runs one always-on
    machine, so in-process scheduling is enough — no external cron needed)."""
    if not config.email_ready():
        log.info("email: SENDGRID_API_KEY / EMAIL_FROM not set — "
                 "onboarding emails disabled")
        return

    def _loop() -> None:
        time.sleep(FIRST_PASS_DELAY_S)
        while True:
            try:
                run_nudge_pass()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.warning("email: nudge pass crashed: %s", exc)
            time.sleep(PASS_INTERVAL_S)

    threading.Thread(target=_loop, daemon=True, name="email-nudge").start()
    log.info("email: onboarding-nudge scheduler started (after %d days, daily pass)",
             config.NUDGE_AFTER_DAYS)

"""
sender.py — Email sending via Resend API or Gmail SMTP.

Sends emails one at a time with safety delays between each send.
Every send is logged and tracked in the database.

Supports two providers:
  - "gmail"  — free, uses Gmail SMTP with an App Password
  - "resend" — Resend API (requires verified custom domain)
"""

import smtplib
import time
import random
import logging
from email.mime.text import MIMEText

from . import outreach_config as cfg
from .safety import check_lead_safety, check_from_address

logger = logging.getLogger("outreach")


def _add_unsubscribe_footer(body: str, email: str) -> str:
    """
    Append a simple unsubscribe line to the email body.

    For a solo developer system, this is a plain-text opt-out notice.
    In a production system, you'd use a link to an unsubscribe endpoint.
    """
    footer = (
        f"\n\n---\n"
        f"If you'd prefer not to hear from us, simply reply with "
        f"\"unsubscribe\" and we'll remove you from our list immediately."
    )
    return body + footer


def _send_via_gmail(to: str, subject: str, body: str, from_field: str) -> tuple[bool, str]:
    """Send an email via Gmail SMTP using an App Password."""
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = from_field
        msg["To"] = to
        msg["Subject"] = subject

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(cfg.FROM_EMAIL, cfg.GMAIL_APP_PASSWORD)
            server.sendmail(cfg.FROM_EMAIL, [to], msg.as_string())

        return True, f"gmail-{to}"

    except smtplib.SMTPAuthenticationError:
        return False, (
            "Gmail authentication failed. Check your App Password. "
            "Generate one at: myaccount.google.com > Security > App Passwords"
        )
    except Exception as e:
        return False, str(e)


def _send_via_resend(to: str, subject: str, body: str, from_field: str) -> tuple[bool, str]:
    """Send an email via the Resend API."""
    try:
        import resend
    except ImportError:
        return False, "resend package not installed (pip install resend)"

    resend.api_key = cfg.RESEND_API_KEY

    try:
        params = {
            "from": from_field,
            "to": [to],
            "subject": subject,
            "text": body,
        }
        result = resend.Emails.send(params)
        message_id = result.get("id", "") if isinstance(result, dict) else str(result)
        return True, message_id

    except Exception as e:
        return False, str(e)


def send_single(lead: dict, db, dry_run: bool = False) -> tuple[bool, str]:
    """
    Send a single email to a lead.

    Args:
        lead: Lead dict from the database.
        db: OutreachDB instance.
        dry_run: If True, simulate the send without actually sending.

    Returns:
        (success, message) where message is the provider ID or error.
    """
    email = lead.get("email", "").strip().lower()
    biz_name = lead.get("business_name", "?")
    subject = lead.get("subject_line", "")
    body = lead.get("email_body", "")

    # Run all safety checks
    safety = check_lead_safety(lead, db)
    if not safety.passed:
        logger.warning("Safety blocked %s (%s): %s", biz_name, email, safety)
        return False, str(safety)

    # Check sender configuration
    from_ok, from_reason = check_from_address()
    if not from_ok and not dry_run:
        logger.error("Sender config error: %s", from_reason)
        return False, from_reason

    # Add unsubscribe footer
    body_with_footer = _add_unsubscribe_footer(body, email)

    # Build the "from" field
    from_field = cfg.FROM_EMAIL
    if cfg.FROM_NAME:
        from_field = f"{cfg.FROM_NAME} <{cfg.FROM_EMAIL}>"

    # --- Dry run mode ---
    if dry_run or cfg.DRY_RUN:
        logger.info("[DRY RUN] Would send to %s (%s)", biz_name, email)
        logger.info("  From: %s", from_field)
        logger.info("  Subject: %s", subject)
        logger.info("  Body preview: %s...", body[:100])
        return True, "dry_run"

    # --- Send via configured provider ---
    provider = cfg.EMAIL_PROVIDER

    if provider == "gmail":
        success, result_msg = _send_via_gmail(email, subject, body_with_footer, from_field)
    else:
        success, result_msg = _send_via_resend(email, subject, body_with_footer, from_field)

    if success:
        logger.info("Sent to %s (%s) via %s — ID: %s", biz_name, email, provider, result_msg)
        db.mark_sent(email, result_msg)
    else:
        logger.error("Send failed for %s (%s) via %s: %s", biz_name, email, provider, result_msg)
        db.mark_failed(email, result_msg)

    return success, result_msg


def send_batch(leads: list[dict], db, dry_run: bool = False) -> tuple[int, int, int]:
    """
    Send emails to a batch of leads with safety delays between sends.

    Respects the daily send cap and pauses between sends.

    Args:
        leads: List of lead dicts ready to send.
        db: OutreachDB instance.
        dry_run: If True, simulate all sends.

    Returns:
        (sent_count, failed_count, skipped_count)
    """
    sent = 0
    failed = 0
    skipped = 0

    from .. import config as main_config

    for i, lead in enumerate(leads):
        email = lead.get("email", "")
        biz_name = lead.get("business_name", "?")

        # Check for shutdown request
        if main_config.is_shutting_down():
            remaining = len(leads) - i
            logger.warning("Shutdown requested. Stopping. %d leads remaining.", remaining)
            skipped += remaining
            break

        # Check daily cap before each send
        if not dry_run and db.count_sent_today() >= cfg.DAILY_SEND_CAP:
            remaining = len(leads) - i
            logger.warning(
                "Daily send cap reached (%d). Stopping. %d leads remaining.",
                cfg.DAILY_SEND_CAP, remaining,
            )
            skipped += remaining
            break

        # Check if campaign was paused mid-batch
        if cfg.CAMPAIGN_PAUSED:
            remaining = len(leads) - i
            logger.warning("Campaign paused. Stopping. %d leads remaining.", remaining)
            skipped += remaining
            break

        logger.info("[%d/%d] Processing %s (%s)", i + 1, len(leads), biz_name, email)

        success, message = send_single(lead, db, dry_run=dry_run)

        if success:
            sent += 1
        elif "BLOCKED" in message:
            skipped += 1
        else:
            failed += 1

        # Delay between sends (skip delay on last item or dry run)
        if i < len(leads) - 1 and not dry_run:
            delay = random.uniform(cfg.MIN_DELAY_SECONDS, cfg.MAX_DELAY_SECONDS)
            logger.info("  Waiting %.0f seconds before next send...", delay)
            time.sleep(delay)

    return sent, failed, skipped

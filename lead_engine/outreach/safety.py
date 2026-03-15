"""
safety.py — Guardrails and validation for outreach sends.

Every email goes through these checks before sending:
  1. Email format validation
  2. Junk email filtering (noreply, generic, bad domains)
  3. Duplicate detection (already sent or in progress)
  4. Opt-out check
  5. Daily send cap enforcement
  6. Campaign pause check
  7. Score threshold check
  8. Approval requirement check
"""

import re
import logging
from datetime import datetime

from . import outreach_config as cfg

logger = logging.getLogger("outreach")

# ---------------------------------------------------------------------------
# Email validation
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

_JUNK_PREFIXES = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "postmaster", "webmaster", "admin", "root", "test", "example",
    "abuse", "hostmaster", "info", "sales", "marketing",
}

_JUNK_DOMAINS = {
    "example.com", "example.org", "test.com", "localhost",
    "wix.com", "wixpress.com", "squarespace.com", "wordpress.com",
    "wordpress.org", "godaddy.com", "sentry.io", "googleapis.com",
    "googleusercontent.com", "gstatic.com", "w3.org", "schema.org",
    "facebook.com", "twitter.com", "instagram.com", "linkedin.com",
    "youtube.com", "tiktok.com", "mailinator.com", "tempmail.com",
    "guerrillamail.com", "throwaway.email",
}


def validate_email(email: str) -> tuple[bool, str]:
    """
    Validate an email address for outreach.

    Returns (is_valid, reason) where reason explains any rejection.
    """
    if not email:
        return False, "empty email"

    email = email.strip().lower()

    # Basic format check
    if not _EMAIL_RE.match(email):
        return False, "invalid email format"

    local, _, domain = email.partition("@")

    # Check junk prefixes
    if local in _JUNK_PREFIXES:
        return False, f"junk prefix: {local}"

    # Check junk domains
    if domain in _JUNK_DOMAINS:
        return False, f"junk domain: {domain}"

    # Check for obviously fake patterns
    if local.startswith("test") or "noreply" in local:
        return False, "suspicious local part"

    # Check for image-like extensions (sometimes scraped from HTML)
    if email.endswith((".png", ".jpg", ".gif", ".svg", ".webp", ".css", ".js")):
        return False, "file extension in email"

    return True, "ok"


# ---------------------------------------------------------------------------
# Pre-send safety checks
# ---------------------------------------------------------------------------
class SafetyCheckResult:
    """Result of running all safety checks on a lead."""

    def __init__(self):
        self.passed = True
        self.reasons: list[str] = []

    def fail(self, reason: str):
        self.passed = False
        self.reasons.append(reason)

    def __str__(self):
        if self.passed:
            return "PASS"
        return f"BLOCKED: {'; '.join(self.reasons)}"


def check_lead_safety(lead: dict, db) -> SafetyCheckResult:
    """
    Run all safety checks on a lead before sending.

    Args:
        lead: Lead dict from the database.
        db: OutreachDB instance for state checks.

    Returns:
        SafetyCheckResult with pass/fail and reasons.
    """
    result = SafetyCheckResult()
    email = (lead.get("email") or "").strip().lower()

    # 1. Campaign paused?
    if cfg.CAMPAIGN_PAUSED:
        result.fail("campaign is paused")
        return result  # Short-circuit — nothing should send

    # 2. Valid email?
    valid, reason = validate_email(email)
    if not valid:
        result.fail(f"email validation: {reason}")

    # 3. Opted out?
    if db.is_opted_out(email):
        result.fail("email is on opt-out list")

    # 4. Already sent?
    if lead.get("status") == "Sent":
        result.fail("already sent")

    # 5. Not approved? (when approval is required)
    if cfg.REQUIRE_APPROVAL and not lead.get("approved_to_send"):
        result.fail("not approved for sending")

    # 6. Score threshold?
    score = lead.get("lead_score", 0)
    if score < cfg.MIN_SCORE_THRESHOLD:
        result.fail(f"score {score} below threshold {cfg.MIN_SCORE_THRESHOLD}")

    # 7. Has draft content?
    if not lead.get("subject_line") or not lead.get("email_body"):
        result.fail("missing subject line or email body")

    # 8. Daily cap?
    sent_today = db.count_sent_today()
    if sent_today >= cfg.DAILY_SEND_CAP:
        result.fail(f"daily cap reached ({sent_today}/{cfg.DAILY_SEND_CAP})")

    # 9. Do Not Contact status?
    if lead.get("status") == "DoNotContact":
        result.fail("marked as Do Not Contact")

    return result


def check_from_address() -> tuple[bool, str]:
    """
    Verify that the FROM email and provider credentials are configured.
    """
    if not cfg.FROM_EMAIL:
        return False, "OUTREACH_FROM_EMAIL not set in .env"

    if cfg.EMAIL_PROVIDER == "gmail":
        if not cfg.GMAIL_APP_PASSWORD:
            return False, (
                "GMAIL_APP_PASSWORD not set in .env. "
                "Generate one at: myaccount.google.com > Security > App Passwords"
            )
    else:
        if not cfg.RESEND_API_KEY:
            return False, "RESEND_API_KEY not set in .env"

    return True, "ok"

"""
outreach_config.py — Configuration for the email outreach system.

All outreach-specific settings live here. Provider keys come from .env.
"""

import os

# ---------------------------------------------------------------------------
# Email provider (Resend)
# ---------------------------------------------------------------------------
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")

# The "From" address — must be verified in your Resend dashboard.
# Use a custom domain like outreach@yourdomain.com for best deliverability.
# Resend also provides a sandbox: onboarding@resend.dev (for testing only).
FROM_EMAIL = os.getenv("OUTREACH_FROM_EMAIL", "")
FROM_NAME = os.getenv("OUTREACH_FROM_NAME", "")

# ---------------------------------------------------------------------------
# Email provider selection — "gmail" or "resend"
# ---------------------------------------------------------------------------
# "gmail"  — Send via Gmail SMTP (free, uses your Gmail + App Password)
# "resend" — Send via Resend API (requires verified custom domain)
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "gmail").lower()

# Gmail SMTP — generate an App Password at:
#   myaccount.google.com → Security → 2-Step Verification → App Passwords
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

# ---------------------------------------------------------------------------
# Safety limits
# ---------------------------------------------------------------------------
DAILY_SEND_CAP = int(os.getenv("OUTREACH_DAILY_CAP", "20"))
MIN_DELAY_SECONDS = int(os.getenv("OUTREACH_MIN_DELAY", "45"))
MAX_DELAY_SECONDS = int(os.getenv("OUTREACH_MAX_DELAY", "90"))
MIN_SCORE_THRESHOLD = int(os.getenv("OUTREACH_MIN_SCORE", "30"))

# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------
# When True, no emails are actually sent — the system logs what it *would* do.
DRY_RUN = os.getenv("OUTREACH_DRY_RUN", "false").lower() in ("true", "1", "yes")

# When True, leads must be manually approved before sending.
# This is the recommended default. Set to False for fully automatic sending.
REQUIRE_APPROVAL = os.getenv("OUTREACH_REQUIRE_APPROVAL", "true").lower() in ("true", "1", "yes")

# When True, the campaign is paused — no sends will happen.
CAMPAIGN_PAUSED = os.getenv("OUTREACH_PAUSED", "false").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# AI message generation
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Your business info — used in email generation so Claude knows who you are.
YOUR_NAME = os.getenv("OUTREACH_YOUR_NAME", "")
YOUR_BUSINESS = os.getenv("OUTREACH_YOUR_BUSINESS", "")
YOUR_SERVICE = os.getenv("OUTREACH_YOUR_SERVICE", "")
YOUR_WEBSITE = os.getenv("OUTREACH_YOUR_WEBSITE", "")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH = os.getenv("OUTREACH_DB_PATH", "output/outreach.db")

# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------
LEAD_EXCEL_PATH = os.getenv("OUTREACH_LEAD_FILE", "output/lead_tracker.xlsx")

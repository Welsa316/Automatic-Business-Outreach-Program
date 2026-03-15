"""
outreach.py — CLI for the email outreach automation system.

Commands:
    python outreach.py ingest              Import leads from Excel and generate drafts
    python outreach.py ingest --file X     Import from a specific file
    python outreach.py drafts              Generate drafts for new leads only
    python outreach.py review              Interactive review of pending drafts
    python outreach.py approve-all         Approve all reviewed leads
    python outreach.py send                Send approved emails
    python outreach.py send --dry-run      Simulate sending (no actual emails)
    python outreach.py status              Show campaign statistics
    python outreach.py list                List all leads with their status
    python outreach.py opt-out EMAIL       Add an email to the opt-out list
"""

import argparse
import signal
import sys
import os
import logging
from pathlib import Path

# ---------------------------------------------------------------------------
# Setup — load .env before importing outreach modules
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
_env_path = BASE_DIR / ".env"
try:
    from dotenv import load_dotenv
    load_dotenv(_env_path)
except ImportError:
    pass

from lead_engine.outreach import outreach_config as cfg
# Re-read keys after dotenv
cfg.EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "gmail").lower()
cfg.GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
cfg.RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
cfg.FROM_EMAIL = os.getenv("OUTREACH_FROM_EMAIL", "")
cfg.FROM_NAME = os.getenv("OUTREACH_FROM_NAME", "")
cfg.ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
cfg.YOUR_NAME = os.getenv("OUTREACH_YOUR_NAME", "")
cfg.YOUR_BUSINESS = os.getenv("OUTREACH_YOUR_BUSINESS", "")
cfg.YOUR_SERVICE = os.getenv("OUTREACH_YOUR_SERVICE", "")
cfg.YOUR_WEBSITE = os.getenv("OUTREACH_YOUR_WEBSITE", "")

from lead_engine.outreach.campaign import (
    ingest_leads,
    generate_all_drafts,
    get_review_queue,
    approve_lead,
    reject_lead,
    approve_all_reviewed,
    send_approved,
    get_campaign_stats,
    get_all_leads,
    add_opt_out,
    run_ingest_pipeline,
)

logger = logging.getLogger("outreach")


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")


# ---------------------------------------------------------------------------
# Command: ingest
# ---------------------------------------------------------------------------
def cmd_ingest(args):
    """Import leads from Excel/CSV and generate AI drafts."""
    file_path = args.file or cfg.LEAD_EXCEL_PATH

    if not Path(file_path).exists():
        print(f"File not found: {file_path}")
        print(f"Run your lead pipeline first, or specify --file path/to/leads.xlsx")
        sys.exit(1)

    print(f"\n=== Ingesting leads from {file_path} ===\n")
    summary = run_ingest_pipeline(file_path)

    print(f"\nResults:")
    print(f"  New leads imported:   {summary['ingested']}")
    print(f"  Duplicates skipped:   {summary['skipped_duplicates']}")
    print(f"  Drafts generated:     {summary['drafts_generated']}")
    print(f"  Draft errors:         {summary['draft_errors']}")

    if summary["drafts_generated"] > 0:
        print(f"\nNext step: Review drafts with 'python outreach.py review'")


# ---------------------------------------------------------------------------
# Command: drafts
# ---------------------------------------------------------------------------
def cmd_drafts(args):
    """Generate drafts for new leads that don't have them yet."""
    if not cfg.ANTHROPIC_API_KEY:
        print("Error: ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    print("\n=== Generating email drafts ===\n")
    success, errors = generate_all_drafts()
    print(f"\nDone: {success} drafts generated, {errors} errors")

    if success > 0:
        print(f"Next step: Review drafts with 'python outreach.py review'")


# ---------------------------------------------------------------------------
# Command: review
# ---------------------------------------------------------------------------
def cmd_review(args):
    """Interactive review and approval of pending drafts."""
    queue = get_review_queue()

    if not queue:
        print("\nNo leads pending review.")
        print("Run 'python outreach.py ingest' to import and generate drafts.")
        return

    print(f"\n=== Review Queue: {len(queue)} leads ===\n")

    for i, lead in enumerate(queue):
        print("=" * 60)
        print(f"[{i + 1}/{len(queue)}] {lead['business_name']}")
        print(f"  Email:    {lead['email']}")
        print(f"  City:     {lead['city']}")
        print(f"  Category: {lead['category']}")
        print(f"  Score:    {lead['lead_score']}")
        print(f"  Rating:   {lead['rating']} ({lead['review_count']} reviews)")
        print()
        print(f"  Subject:  {lead['subject_line']}")
        print()
        print("  --- Email Body ---")
        for line in lead["email_body"].split("\n"):
            print(f"  {line}")
        print("  --- End ---")
        print()

        while True:
            choice = input("  [A]pprove / [R]eject / [S]kip / [Q]uit: ").strip().lower()
            if choice in ("a", "approve"):
                approve_lead(lead["email"])
                print("  -> Approved")
                break
            elif choice in ("r", "reject"):
                reason = input("  Reason (optional): ").strip()
                reject_lead(lead["email"], reason)
                print("  -> Rejected")
                break
            elif choice in ("s", "skip"):
                print("  -> Skipped (will appear again next review)")
                break
            elif choice in ("q", "quit"):
                print("\nExiting review.")
                return
            else:
                print("  Invalid choice. Use A, R, S, or Q.")

        print()

    print("Review complete.")
    stats = get_campaign_stats()
    approved = stats.get("approved", 0)
    if approved > 0:
        print(f"\n{approved} leads approved. Send with: python outreach.py send")


# ---------------------------------------------------------------------------
# Command: approve-all
# ---------------------------------------------------------------------------
def cmd_approve_all(args):
    """Approve all leads that have generated drafts."""
    count = approve_all_reviewed()
    print(f"\nApproved {count} leads.")
    if count > 0:
        print(f"Send with: python outreach.py send")


# ---------------------------------------------------------------------------
# Command: send
# ---------------------------------------------------------------------------
def cmd_send(args):
    """Send emails to approved leads."""
    dry_run = args.dry_run

    if dry_run:
        print("\n=== DRY RUN MODE — No emails will actually be sent ===\n")
    else:
        # Final confirmation before real sends
        stats = get_campaign_stats()
        approved = stats.get("approved", 0)
        if approved == 0:
            print("\nNo approved leads to send.")
            print("Review leads first: python outreach.py review")
            return

        print(f"\n=== Ready to send {approved} emails ===")
        print(f"  Daily cap:      {cfg.DAILY_SEND_CAP}")
        print(f"  Delay:          {cfg.MIN_DELAY_SECONDS}-{cfg.MAX_DELAY_SECONDS}s between sends")
        print(f"  From:           {cfg.FROM_NAME} <{cfg.FROM_EMAIL}>")
        print(f"  Sent today:     {stats.get('sent_today', 0)}")
        print()

        confirm = input("Proceed with sending? (yes/no): ").strip().lower()
        if confirm != "yes":
            print("Cancelled.")
            return

    print()
    sent, failed, skipped = send_approved(dry_run=dry_run)

    print(f"\n=== Send Results ===")
    print(f"  Sent:    {sent}")
    print(f"  Failed:  {failed}")
    print(f"  Skipped: {skipped}")

    if not dry_run and sent > 0:
        print(f"\nEmails sent successfully. Check 'python outreach.py status' for details.")


# ---------------------------------------------------------------------------
# Command: status
# ---------------------------------------------------------------------------
def cmd_status(args):
    """Show campaign statistics."""
    stats = get_campaign_stats()

    print("\n=== Campaign Status ===\n")
    print(f"  Total leads:      {stats.get('total', 0)}")
    print(f"  ---")
    print(f"  New:              {stats.get('new', 0)}")
    print(f"  Reviewed:         {stats.get('reviewed', 0)}")
    print(f"  Approved:         {stats.get('approved', 0)}")
    print(f"  Rejected:         {stats.get('rejected', 0)}")
    print(f"  Queued:           {stats.get('queued', 0)}")
    print(f"  Sent:             {stats.get('sent', 0)}")
    print(f"  Failed:           {stats.get('failed', 0)}")
    print(f"  Replied:          {stats.get('replied', 0)}")
    print(f"  Do Not Contact:   {stats.get('donotcontact', 0)}")
    print(f"  Follow-Up Due:    {stats.get('followupdue', 0)}")
    print(f"  ---")
    print(f"  Sent today:       {stats.get('sent_today', 0)} / {cfg.DAILY_SEND_CAP}")
    print(f"  Opted out:        {stats.get('opted_out', 0)}")
    print()

    # Configuration info
    print("  Configuration:")
    print(f"    Dry run:          {'YES' if cfg.DRY_RUN else 'no'}")
    print(f"    Approval required: {'YES' if cfg.REQUIRE_APPROVAL else 'no'}")
    print(f"    Campaign paused:  {'YES' if cfg.CAMPAIGN_PAUSED else 'no'}")
    print(f"    Min score:        {cfg.MIN_SCORE_THRESHOLD}")
    print(f"    Daily cap:        {cfg.DAILY_SEND_CAP}")
    print(f"    Send delay:       {cfg.MIN_DELAY_SECONDS}-{cfg.MAX_DELAY_SECONDS}s")
    print()


# ---------------------------------------------------------------------------
# Command: list
# ---------------------------------------------------------------------------
def cmd_list(args):
    """List all leads with their current status."""
    leads = get_all_leads()

    if not leads:
        print("\nNo leads in database. Run 'python outreach.py ingest' first.")
        return

    # Optional status filter
    status_filter = args.filter.strip() if hasattr(args, "filter") and args.filter else ""

    if status_filter:
        leads = [l for l in leads if l["status"].lower() == status_filter.lower()]
        print(f"\n=== Leads with status '{status_filter}': {len(leads)} ===\n")
    else:
        print(f"\n=== All Leads: {len(leads)} ===\n")

    # Table header
    print(f"  {'Score':>5}  {'Status':<14}  {'Business':<30}  {'Email':<35}  {'City':<15}")
    print(f"  {'-----':>5}  {'------':<14}  {'--------':<30}  {'-----':<35}  {'----':<15}")

    for lead in leads:
        name = lead["business_name"][:28]
        email = lead["email"][:33]
        city = (lead["city"] or "")[:13]
        print(f"  {lead['lead_score']:>5}  {lead['status']:<14}  {name:<30}  {email:<35}  {city:<15}")

    print()


# ---------------------------------------------------------------------------
# Command: opt-out
# ---------------------------------------------------------------------------
def cmd_opt_out(args):
    """Add an email to the opt-out list."""
    email = args.email.strip().lower()
    reason = args.reason or "manual opt-out"

    add_opt_out(email, reason)
    print(f"\nAdded {email} to opt-out list.")
    print("This lead will never be contacted.")


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Email Outreach Automation — Safe, semi-automated outreach",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Typical workflow:
  1. python outreach.py ingest                  # Import leads + generate drafts
  2. python outreach.py review                  # Approve or reject each draft
  3. python outreach.py send --dry-run          # Test without sending
  4. python outreach.py send                    # Send for real
  5. python outreach.py status                  # Check results
        """,
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ingest
    p_ingest = subparsers.add_parser("ingest", help="Import leads and generate drafts")
    p_ingest.add_argument("--file", type=str, default="",
                          help="Path to lead file (default: output/lead_tracker.xlsx)")

    # drafts
    subparsers.add_parser("drafts", help="Generate drafts for new leads")

    # review
    subparsers.add_parser("review", help="Interactive review of pending drafts")

    # approve-all
    subparsers.add_parser("approve-all", help="Approve all reviewed leads at once")

    # send
    p_send = subparsers.add_parser("send", help="Send emails to approved leads")
    p_send.add_argument("--dry-run", action="store_true",
                        help="Simulate sending without actually sending emails")

    # status
    subparsers.add_parser("status", help="Show campaign statistics")

    # list
    p_list = subparsers.add_parser("list", help="List all leads")
    p_list.add_argument("--filter", type=str, default="",
                        help="Filter by status (e.g., Approved, Sent, Failed)")

    # opt-out
    p_optout = subparsers.add_parser("opt-out", help="Add email to opt-out list")
    p_optout.add_argument("email", type=str, help="Email address to opt out")
    p_optout.add_argument("--reason", type=str, default="", help="Reason for opt-out")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Dispatch
    commands = {
        "ingest": cmd_ingest,
        "drafts": cmd_drafts,
        "review": cmd_review,
        "approve-all": cmd_approve_all,
        "send": cmd_send,
        "status": cmd_status,
        "list": cmd_list,
        "opt-out": cmd_opt_out,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


def _install_signal_handlers() -> None:
    """Install signal handlers for graceful shutdown on Ctrl+C."""
    from lead_engine import config as main_config

    def _handler(signum, frame):
        print("\n\n*** Shutdown requested — finishing current task... ***")
        print("*** Press Ctrl+C again to force quit. ***\n")
        main_config.request_shutdown()
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)


if __name__ == "__main__":
    _install_signal_handlers()
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
    except Exception as exc:
        print(f"\nError: {exc}")
        logging.exception("Unhandled error")

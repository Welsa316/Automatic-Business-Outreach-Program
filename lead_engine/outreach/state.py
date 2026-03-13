"""
state.py — SQLite-backed state management for outreach leads.

Tracks every lead through its lifecycle:
  New → Reviewed → Approved → Queued → Sent
                 → Rejected
                              → Failed (retryable)
  Sent → Replied / FollowUpDue
  Any  → DoNotContact (permanent exclusion)

The database is the single source of truth for what has been sent.
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path

from . import outreach_config as cfg

logger = logging.getLogger("outreach")

# Valid lead statuses
VALID_STATUSES = {
    "New",
    "Reviewed",
    "Approved",
    "Rejected",
    "Queued",
    "Sent",
    "Failed",
    "Replied",
    "DoNotContact",
    "FollowUpDue",
}

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS leads (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name       TEXT NOT NULL,
    email               TEXT NOT NULL UNIQUE,
    website             TEXT DEFAULT '',
    city                TEXT DEFAULT '',
    category            TEXT DEFAULT '',
    lead_score          INTEGER DEFAULT 0,
    phone               TEXT DEFAULT '',
    rating              REAL DEFAULT 0.0,
    review_count        INTEGER DEFAULT 0,
    status              TEXT DEFAULT 'New',
    subject_line        TEXT DEFAULT '',
    email_body          TEXT DEFAULT '',
    reviewed_by_human   INTEGER DEFAULT 0,
    approved_to_send    INTEGER DEFAULT 0,
    sent_at             TEXT DEFAULT '',
    last_error          TEXT DEFAULT '',
    provider_message_id TEXT DEFAULT '',
    notes               TEXT DEFAULT '',
    follow_up_due_date  TEXT DEFAULT '',
    opt_out             INTEGER DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
)
"""

_CREATE_SEND_LOG = """
CREATE TABLE IF NOT EXISTS send_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL,
    email       TEXT NOT NULL,
    sent_at     TEXT NOT NULL,
    status      TEXT NOT NULL,
    message_id  TEXT DEFAULT '',
    error       TEXT DEFAULT '',
    FOREIGN KEY (lead_id) REFERENCES leads(id)
)
"""

_CREATE_OPT_OUTS = """
CREATE TABLE IF NOT EXISTS opt_outs (
    email       TEXT PRIMARY KEY,
    added_at    TEXT NOT NULL,
    reason      TEXT DEFAULT ''
)
"""


def _now() -> str:
    """Current timestamp as ISO string."""
    return datetime.now().isoformat(timespec="seconds")


class OutreachDB:
    """SQLite database for outreach state management."""

    def __init__(self, db_path: str = ""):
        path = db_path or cfg.DB_PATH
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()
        logger.info("Outreach DB ready: %s", path)

    def _init_tables(self):
        """Create tables if they don't exist."""
        with self.conn:
            self.conn.execute(_CREATE_TABLE)
            self.conn.execute(_CREATE_SEND_LOG)
            self.conn.execute(_CREATE_OPT_OUTS)

    def close(self):
        """Close the database connection."""
        self.conn.close()

    # ------------------------------------------------------------------
    # Lead ingestion
    # ------------------------------------------------------------------
    def ingest_lead(self, lead: dict) -> bool:
        """
        Insert a new lead. Returns True if inserted, False if duplicate.

        Skips leads that already exist (by email) or are in opt-out list.
        """
        email = (lead.get("email") or "").strip().lower()
        if not email:
            return False

        # Check opt-out list
        if self.is_opted_out(email):
            logger.debug("Skipping opted-out email: %s", email)
            return False

        now = _now()
        try:
            with self.conn:
                self.conn.execute(
                    """INSERT INTO leads
                       (business_name, email, website, city, category,
                        lead_score, phone, rating, review_count,
                        status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'New', ?, ?)""",
                    (
                        lead.get("business_name", ""),
                        email,
                        lead.get("website", ""),
                        lead.get("city", ""),
                        lead.get("primary_category", lead.get("category", "")),
                        int(lead.get("lead_score", 0)),
                        lead.get("phone", ""),
                        float(lead.get("rating", 0.0)),
                        int(lead.get("review_count", 0)),
                        now, now,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            # Duplicate email — skip silently
            logger.debug("Duplicate email skipped: %s", email)
            return False

    def ingest_many(self, leads: list[dict]) -> tuple[int, int]:
        """
        Ingest multiple leads. Returns (inserted_count, skipped_count).
        """
        inserted = 0
        skipped = 0
        for lead in leads:
            if self.ingest_lead(lead):
                inserted += 1
            else:
                skipped += 1
        return inserted, skipped

    # ------------------------------------------------------------------
    # Status management
    # ------------------------------------------------------------------
    def update_status(self, email: str, new_status: str, **kwargs):
        """
        Update a lead's status and optional fields.

        kwargs can include: subject_line, email_body, notes, last_error,
        provider_message_id, follow_up_due_date, reviewed_by_human,
        approved_to_send, sent_at, opt_out.
        """
        if new_status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {new_status}. Must be one of {VALID_STATUSES}")

        sets = ["status = ?", "updated_at = ?"]
        vals = [new_status, _now()]

        allowed_fields = {
            "subject_line", "email_body", "notes", "last_error",
            "provider_message_id", "follow_up_due_date",
            "reviewed_by_human", "approved_to_send", "sent_at", "opt_out",
        }
        for field, value in kwargs.items():
            if field in allowed_fields:
                sets.append(f"{field} = ?")
                vals.append(value)

        vals.append(email.strip().lower())
        sql = f"UPDATE leads SET {', '.join(sets)} WHERE email = ?"
        with self.conn:
            self.conn.execute(sql, vals)

    def mark_approved(self, email: str, notes: str = ""):
        """Approve a lead for sending."""
        self.update_status(
            email, "Approved",
            reviewed_by_human=1,
            approved_to_send=1,
            notes=notes,
        )

    def mark_rejected(self, email: str, notes: str = ""):
        """Reject a lead — will not be sent."""
        self.update_status(
            email, "Rejected",
            reviewed_by_human=1,
            approved_to_send=0,
            notes=notes,
        )

    def mark_sent(self, email: str, message_id: str = ""):
        """Record a successful send."""
        now = _now()
        self.update_status(
            email, "Sent",
            sent_at=now,
            provider_message_id=message_id,
            last_error="",
        )
        # Also log to send_log
        lead = self.get_lead(email)
        if lead:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO send_log (lead_id, email, sent_at, status, message_id) "
                    "VALUES (?, ?, ?, 'sent', ?)",
                    (lead["id"], email, now, message_id),
                )

    def mark_failed(self, email: str, error: str = ""):
        """Record a failed send attempt."""
        self.update_status(email, "Failed", last_error=error)
        lead = self.get_lead(email)
        if lead:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO send_log (lead_id, email, sent_at, status, error) "
                    "VALUES (?, ?, ?, 'failed', ?)",
                    (lead["id"], email, _now(), error),
                )

    def mark_do_not_contact(self, email: str, reason: str = ""):
        """Permanently exclude a lead."""
        self.update_status(email, "DoNotContact", notes=reason)
        self.add_opt_out(email, reason)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get_lead(self, email: str) -> dict | None:
        """Get a single lead by email."""
        row = self.conn.execute(
            "SELECT * FROM leads WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
        return dict(row) if row else None

    def get_leads_by_status(self, status: str) -> list[dict]:
        """Get all leads with a given status, ordered by score descending."""
        rows = self.conn.execute(
            "SELECT * FROM leads WHERE status = ? ORDER BY lead_score DESC",
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_sendable_leads(self) -> list[dict]:
        """
        Get leads that are ready to send.
        Must be Approved status with a subject line and email body.
        """
        rows = self.conn.execute(
            """SELECT * FROM leads
               WHERE status = 'Approved'
                 AND approved_to_send = 1
                 AND opt_out = 0
                 AND subject_line != ''
                 AND email_body != ''
               ORDER BY lead_score DESC""",
        ).fetchall()
        return [dict(r) for r in rows]

    def get_new_leads(self, min_score: int = 0) -> list[dict]:
        """Get new leads above a score threshold."""
        rows = self.conn.execute(
            "SELECT * FROM leads WHERE status = 'New' AND lead_score >= ? "
            "ORDER BY lead_score DESC",
            (min_score,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_leads_needing_review(self) -> list[dict]:
        """Get leads with generated drafts that need human review."""
        rows = self.conn.execute(
            """SELECT * FROM leads
               WHERE status = 'Reviewed'
                 AND reviewed_by_human = 0
                 AND subject_line != ''
                 AND email_body != ''
               ORDER BY lead_score DESC""",
        ).fetchall()
        return [dict(r) for r in rows]

    def count_sent_today(self) -> int:
        """Count how many emails were sent today (for daily cap)."""
        today = datetime.now().strftime("%Y-%m-%d")
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM send_log WHERE sent_at LIKE ? AND status = 'sent'",
            (f"{today}%",),
        ).fetchone()
        return row["cnt"] if row else 0

    def get_all_leads(self) -> list[dict]:
        """Get all leads ordered by score."""
        rows = self.conn.execute(
            "SELECT * FROM leads ORDER BY lead_score DESC",
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """Get summary statistics for the campaign."""
        total = self.conn.execute("SELECT COUNT(*) as c FROM leads").fetchone()["c"]
        stats = {"total": total}
        for status in VALID_STATUSES:
            row = self.conn.execute(
                "SELECT COUNT(*) as c FROM leads WHERE status = ?", (status,)
            ).fetchone()
            stats[status.lower()] = row["c"]
        stats["sent_today"] = self.count_sent_today()
        stats["opted_out"] = self.conn.execute(
            "SELECT COUNT(*) as c FROM opt_outs"
        ).fetchone()["c"]
        return stats

    # ------------------------------------------------------------------
    # Opt-out management
    # ------------------------------------------------------------------
    def add_opt_out(self, email: str, reason: str = ""):
        """Add an email to the opt-out list."""
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR IGNORE INTO opt_outs (email, added_at, reason) VALUES (?, ?, ?)",
                    (email.strip().lower(), _now(), reason),
                )
        except sqlite3.IntegrityError:
            pass

    def is_opted_out(self, email: str) -> bool:
        """Check if an email is on the opt-out list."""
        row = self.conn.execute(
            "SELECT 1 FROM opt_outs WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
        return row is not None

    def remove_opt_out(self, email: str):
        """Remove an email from the opt-out list (use with caution)."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM opt_outs WHERE email = ?",
                (email.strip().lower(),),
            )

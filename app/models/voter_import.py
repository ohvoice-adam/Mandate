"""
VoterImport model — tracks the state of long-running voter file import jobs.

Concepts used here:
- **ImportStatus constants**: plain class attributes used as an enum-like
  namespace.  Python's ``enum.Enum`` would work too, but a simple class is
  easier to compare with string DB values (``status == ImportStatus.RUNNING``).
- **cancel_requested column**: the web request sets this flag to True in the
  DB; the background import thread polls it each batch and exits when it sees
  True.  This is the standard pattern for cooperative cancellation across
  process boundaries (a threading.Event can't cross a DB connection).
"""

from datetime import datetime
from app import db


class ImportStatus:
    """
    String constants for the ``VoterImport.status`` column.

    Using a class instead of a Python Enum means the values stored in the DB
    are plain strings (``"running"``, ``"completed"``, etc.) that are easy to
    inspect, query, and display without any conversion step.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VoterImport(db.Model):
    """Track voter file import jobs with progress and rollback support."""

    __tablename__ = "voter_imports"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    county_name = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default=ImportStatus.PENDING, nullable=False)

    total_rows = db.Column(db.Integer, default=0)
    processed_rows = db.Column(db.Integer, default=0)
    error_message = db.Column(db.Text)

    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # For rollback: highest voter ID before this import started
    rollback_voter_id = db.Column(db.Integer)
    # Name of backup table (voters_backup_{import_id})
    backup_table = db.Column(db.String(100))
    # Detected county_number from CSV (for rollback)
    detected_county_ids = db.Column(db.Text)

    # Flag to signal cancellation to the running import thread
    cancel_requested = db.Column(db.Boolean, default=False)

    @property
    def percent_complete(self):
        if self.total_rows == 0:
            return 0
        return round((self.processed_rows / self.total_rows) * 100, 1)

    @property
    def is_running(self):
        return self.status == ImportStatus.RUNNING

    @property
    def is_completed(self):
        return self.status == ImportStatus.COMPLETED

    @property
    def is_failed(self):
        return self.status == ImportStatus.FAILED

    @property
    def is_cancelled(self):
        return self.status == ImportStatus.CANCELLED

    @property
    def can_rollback(self):
        """Check if this import can be rolled back (completed within 24 hours)."""
        if self.status != ImportStatus.COMPLETED:
            return False
        if not self.completed_at:
            return False
        hours_since = (datetime.utcnow() - self.completed_at).total_seconds() / 3600
        return hours_since < 24

    @property
    def status_display(self):
        return self.status.replace("_", " ").title()

    def to_status_dict(self):
        """Return status info for JSON polling response."""
        return {
            "id": self.id,
            "status": self.status,
            "processed_rows": self.processed_rows,
            "total_rows": self.total_rows,
            "percent": self.percent_complete,
            "error_message": self.error_message,
            "can_rollback": self.can_rollback,
        }

    def __repr__(self):
        return f"<VoterImport {self.id}: {self.filename} ({self.status})>"

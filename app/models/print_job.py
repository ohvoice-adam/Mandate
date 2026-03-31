"""
PetitionPrintJob model — stores generated petition PDFs in the database.

PDFs are stored base64-encoded in a ``Text`` column rather than on disk so
that there is no separate file-storage dependency.  The trade-off is that
the DB rows are large (~1 MB+ per job) and the column must be decoded before
use.  ``get_pdf_bytes()`` handles the decode step.

Note: ``foreign_keys=[generated_by_id]`` is required when SQLAlchemy cannot
automatically determine which foreign key to use for a relationship — here
there is only one, but being explicit avoids an ambiguity warning.
"""

import base64
from datetime import datetime
from app import db


class PetitionPrintJob(db.Model):
    """A batch of serialized petition PDFs generated in one print run."""

    __tablename__ = "petition_print_jobs"
    id = db.Column(db.Integer, primary_key=True)
    # default=datetime.utcnow (without calling it) passes the *function*
    # to SQLAlchemy, which calls it at INSERT time — not at class definition.
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    start_number = db.Column(db.Integer, nullable=False)
    end_number = db.Column(db.Integer, nullable=False)
    page_count = db.Column(db.Integer, nullable=False)
    generated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    filename = db.Column(db.String(255), nullable=False)
    # The full PDF is stored as base64-encoded text to avoid filesystem deps.
    # Use get_pdf_bytes() to decode before sending to the browser.
    pdf_content = db.Column(db.Text, nullable=False)  # base64-encoded

    # foreign_keys= disambiguates which FK column to use for this relationship
    # (required when a model has multiple FKs pointing to the same table).
    generated_by = db.relationship("User", foreign_keys=[generated_by_id])

    @property
    def book_count(self):
        return self.end_number - self.start_number + 1

    def get_pdf_bytes(self) -> bytes:
        return base64.b64decode(self.pdf_content)

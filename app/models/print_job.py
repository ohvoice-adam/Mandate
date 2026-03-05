import base64
from datetime import datetime
from app import db


class PetitionPrintJob(db.Model):
    __tablename__ = "petition_print_jobs"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    start_number = db.Column(db.Integer, nullable=False)
    end_number = db.Column(db.Integer, nullable=False)
    page_count = db.Column(db.Integer, nullable=False)
    generated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    filename = db.Column(db.String(255), nullable=False)
    pdf_content = db.Column(db.Text, nullable=False)  # base64-encoded

    generated_by = db.relationship("User", foreign_keys=[generated_by_id])

    @property
    def book_count(self):
        return self.end_number - self.start_number + 1

    def get_pdf_bytes(self) -> bytes:
        return base64.b64decode(self.pdf_content)

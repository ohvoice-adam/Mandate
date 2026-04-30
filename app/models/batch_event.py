from datetime import datetime
from app import db


class BatchEvent(db.Model):
    """Audit log for batch-level actions (rollbacks, etc.)."""

    __tablename__ = "batch_events"

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("batches.id", ondelete="SET NULL"), nullable=True)
    action = db.Column(db.String(50), nullable=False)
    performed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    performed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    signatures_deleted = db.Column(db.Integer, default=0)
    note = db.Column(db.Text)

    batch = db.relationship("Batch", back_populates="events")
    performed_by = db.relationship("User")

    def __repr__(self):
        return f"<BatchEvent {self.action} on batch {self.batch_id}>"

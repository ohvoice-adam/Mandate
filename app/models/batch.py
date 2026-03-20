from app import db


class Batch(db.Model):
    """Data entry batches - tracks who entered data from which book."""

    __tablename__ = "batches"

    id = db.Column(db.Integer, primary_key=True)
    book_id = db.Column(db.Integer, db.ForeignKey("books.id"), nullable=False)
    book_number = db.Column(db.String(50))
    collector_id = db.Column(db.Integer, db.ForeignKey("collectors.id"))
    enterer_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    enterer_first = db.Column(db.String(100))
    enterer_last = db.Column(db.String(100))
    enterer_email = db.Column(db.String(120))
    date_entered = db.Column(db.Date)
    status = db.Column(db.String(20), nullable=False, default="open")
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    # Relationships
    book = db.relationship("Book", back_populates="batches")
    collector = db.relationship("Collector")
    enterer = db.relationship("User")
    signatures = db.relationship("Signature", back_populates="batch")

    def __repr__(self):
        return f"<Batch {self.id} - Book {self.book_number}>"

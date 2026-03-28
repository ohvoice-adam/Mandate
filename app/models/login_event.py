from app import db


class UserLoginEvent(db.Model):
    __tablename__ = "user_login_events"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    logged_in_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False, index=True)
    ip_address = db.Column(db.String(45), nullable=True)  # supports IPv6

    user = db.relationship("User", back_populates="login_events")

"""
UserLoginEvent model — append-only login history for the system health page.

One row is inserted each time a user successfully authenticates.  Rows are
never updated; the system health dashboard queries recent rows to show active
users.

SQLAlchemy concept:
- ``ondelete="CASCADE"`` in ``db.ForeignKey()`` adds ``ON DELETE CASCADE``
  to the DDL, so the DB automatically deletes login events when their parent
  user is deleted — without Python needing to do it explicitly.  Combined with
  ``cascade="all, delete-orphan"`` on the relationship in User, both the
  Python session and the DB enforce the constraint.
"""

from app import db


class UserLoginEvent(db.Model):
    """Records a single successful login event for a user."""

    __tablename__ = "user_login_events"

    id = db.Column(db.Integer, primary_key=True)
    # ondelete="CASCADE": if the parent user row is deleted, all their login
    # events are automatically deleted by the DB engine as well.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    logged_in_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False, index=True)
    ip_address = db.Column(db.String(45), nullable=True)  # supports IPv6

    # back_populates="login_events" keeps user.login_events in sync with this
    # side of the relationship.
    user = db.relationship("User", back_populates="login_events")

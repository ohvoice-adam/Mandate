from functools import wraps

from flask import abort, flash, redirect, url_for
from flask_login import UserMixin, current_user
from werkzeug.security import generate_password_hash, check_password_hash

from app import db, login_manager


class UserRole:
    """User role constants."""
    ENTERER = "enterer"
    ORGANIZER = "organizer"
    ADMIN = "admin"

    CHOICES = [
        (ENTERER, "Data Enterer"),
        (ORGANIZER, "Organizer"),
        (ADMIN, "Administrator"),
    ]


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256))
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    role = db.Column(db.String(20), default=UserRole.ENTERER, nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True)
    _is_active = db.Column("is_active", db.Boolean, default=True)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    last_seen = db.Column(db.DateTime, nullable=True)

    @property
    def is_active(self):
        return bool(self._is_active)

    @is_active.setter
    def is_active(self, value):
        self._is_active = value

    created_at = db.Column(db.DateTime, server_default=db.func.now())

    # Relationships
    organization = db.relationship("Organization", back_populates="users")
    login_events = db.relationship("UserLoginEvent", back_populates="user", cascade="all, delete-orphan", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def is_admin(self):
        return self.role == UserRole.ADMIN

    @property
    def is_organizer(self):
        return self.role == UserRole.ORGANIZER

    @property
    def is_admin_or_organizer(self):
        return self.role in (UserRole.ADMIN, UserRole.ORGANIZER)

    @property
    def role_display(self):
        for value, label in UserRole.CHOICES:
            if value == self.role:
                return label
        return self.role

    def __repr__(self):
        return f"<User {self.email}>"


def admin_required(f):
    """Decorator to require admin role for a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not current_user.is_admin:
            flash("You don't have permission to access this page.", "error")
            return redirect(url_for("main.index"))
        return f(*args, **kwargs)
    return decorated_function


def organizer_required(f):
    """Decorator to require admin or organizer role for a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not current_user.is_admin_or_organizer:
            flash("You don't have permission to access this page.", "error")
            return redirect(url_for("main.index"))
        return f(*args, **kwargs)
    return decorated_function


@login_manager.user_loader
def load_user(id):
    return db.session.get(User, int(id))

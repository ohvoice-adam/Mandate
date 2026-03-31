"""
User model, role constants, access-control decorators, and the Flask-Login
user-loader callback.

Flask / library concepts used here:
- **UserMixin**: mixin from Flask-Login that provides the four attributes
  Flask-Login requires on every user object (``is_authenticated``,
  ``is_active``, ``is_anonymous``, ``get_id()``).
- **@login_manager.user_loader**: a callback Flask-Login calls on *every*
  request to reconstruct the current user from the ID stored in the session.
- **current_user**: a Flask-Login thread-local proxy that always holds the
  User returned by the user_loader (or an AnonymousUser if not logged in).
- **@wraps(f)**: standard Python decorator helper that preserves the wrapped
  function's ``__name__`` — important because Flask uses the function name as
  the endpoint name.
- **generate_password_hash / check_password_hash**: Werkzeug utilities that
  bcrypt-hash passwords so plain-text passwords are never stored.
"""

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
    """
    Application user — data enterers, organizers, and admins.

    Inherits from ``UserMixin`` so Flask-Login can manage authentication
    state, and from ``db.Model`` so SQLAlchemy maps it to the ``users`` table.
    The combination means a single ``User`` object satisfies both the ORM and
    the auth layer simultaneously.
    """

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    # index=True adds a B-tree index so login queries (filter by email) are fast.
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    # Never store plain-text passwords — set_password() hashes before saving.
    password_hash = db.Column(db.String(256))
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    role = db.Column(db.String(20), default=UserRole.ENTERER, nullable=False)
    # ForeignKey("organizations.id") creates a DB-level constraint; SQLAlchemy
    # uses it to build the JOIN when you access user.organization.
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=True)

    # The column is stored as "is_active" in the DB but accessed as _is_active
    # in Python to avoid conflicting with UserMixin's is_active property below.
    _is_active = db.Column("is_active", db.Boolean, default=True)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    # Updated by the before_request hook in __init__.py (throttled to 1/min).
    last_seen = db.Column(db.DateTime, nullable=True)

    @property
    def is_active(self):
        # UserMixin expects an is_active *property*, not a plain column.
        # Wrapping it here lets SQLAlchemy store the value while Flask-Login
        # reads it through the property interface it requires.
        return bool(self._is_active)

    @is_active.setter
    def is_active(self, value):
        self._is_active = value

    # server_default=db.func.now() lets PostgreSQL set the timestamp at INSERT
    # time rather than in Python, which avoids timezone confusion.
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    # db.relationship() tells SQLAlchemy to load the related object(s)
    # automatically.  back_populates keeps both sides in sync:
    # user.organization.users will include this user without a second query.
    organization = db.relationship("Organization", back_populates="users")
    # lazy="dynamic" returns a Query object instead of a list, so callers can
    # chain .filter(), .order_by(), etc. without loading all rows into memory.
    # cascade="all, delete-orphan" means login_events are deleted automatically
    # when the parent user is deleted.
    login_events = db.relationship("UserLoginEvent", back_populates="user", cascade="all, delete-orphan", lazy="dynamic")

    def set_password(self, password):
        """Hash *password* with bcrypt and store it.  Never stores plain text."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Return True if *password* matches the stored hash."""
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
    """
    Route decorator that restricts access to users with the ``admin`` role.

    Usage::

        @bp.route("/admin-only")
        @admin_required
        def admin_only_view():
            ...

    Redirects unauthenticated visitors to the login page.  Shows a flash
    error and redirects authenticated non-admins to the main index.

    Note: ``@wraps(f)`` is required so that Flask sees the original function
    name as the endpoint name, not ``decorated_function``.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            # redirect() sends HTTP 302; url_for() resolves endpoint → URL
            return redirect(url_for("auth.login"))
        if not current_user.is_admin:
            # flash() stores a one-time message in the session; base.html
            # renders it on the next page load and then discards it.
            flash("You don't have permission to access this page.", "error")
            return redirect(url_for("main.index"))
        return f(*args, **kwargs)
    return decorated_function


def organizer_required(f):
    """
    Route decorator that restricts access to organizer *or* admin users.

    Same mechanics as ``admin_required`` above — see its docstring.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not current_user.is_admin_or_organizer:
            flash("You don't have permission to access this page.", "error")
            return redirect(url_for("main.index"))
        return f(*args, **kwargs)
    return decorated_function


# @login_manager.user_loader registers load_user() as the callback Flask-Login
# calls on *every* request.  It reads the user ID from the signed session
# cookie and calls this function to fetch the full User object from the DB.
# Whatever this function returns becomes current_user for that request.
@login_manager.user_loader
def load_user(id):
    """
    Reload a User from the database by primary key.

    Called by Flask-Login on every authenticated request.  Returns None if
    the user no longer exists (which Flask-Login treats as "not logged in").

    Args:
        id: The user ID stored in the session cookie (always a string).
    """
    # db.session.get() is the preferred way to fetch by primary key —
    # it checks the session identity map first before hitting the DB.
    return db.session.get(User, int(id))

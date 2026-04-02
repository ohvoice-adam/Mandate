"""
Mandate application package — entry point and app factory.

Flask concepts used here:
- **App factory** (``create_app()``): instead of creating ``app`` at import
  time, we return a fresh ``Flask`` instance from a function.  This lets
  tests create isolated app instances with different configs.
- **Extensions** (``db``, ``login_manager``, ``migrate``, ``csrf``): created
  at module level *without* an app so they can be imported anywhere.  Bound
  to a specific app later via ``extension.init_app(app)``.
- **Blueprints**: each routes file registers itself as a ``Blueprint``; this
  file imports and mounts them with ``app.register_blueprint()``.  Think of
  blueprints as Django apps — isolated URL namespaces with their own views.
- **Context processor** (``inject_globals``): a function decorated with
  ``@app.context_processor`` whose return dict is merged into every Jinja2
  template's variable namespace automatically.
- **Before-request hook** (``enforce_password_change``): runs before *every*
  incoming request; used here to redirect users with a forced password reset.
"""

__version__ = "0.5.0"

DEFAULT_PRIMARY = "#0c3e6b"
DEFAULT_ACCENT = "#f56708"

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate

from app.config import Config

from flask_wtf.csrf import CSRFProtect

# Extension objects are created here at module level so that any file can do
# `from app import db` and get the same SQLAlchemy instance.  They have no
# app bound yet — that happens inside create_app() via .init_app().
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
# login_view tells Flask-Login which endpoint to redirect to when an
# unauthenticated user hits a @login_required route.
login_manager.login_view = "auth.login"
csrf = CSRFProtect()


def create_app(config_class=Config):
    """
    Application factory — creates and fully configures a Flask app instance.

    Called once at startup (by ``run.py`` or ``flask`` CLI) and again by the
    test suite for each isolated test session.  Accepts an optional
    ``config_class`` so tests can pass a ``TestConfig`` with an in-memory DB.

    Args:
        config_class: A class whose UPPER_CASE attributes become Flask config
                      values.  Defaults to ``Config`` from ``app/config.py``.

    Returns:
        A fully configured ``Flask`` application instance.
    """
    # Flask(__name__) tells Flask where to find templates and static files
    # relative to this package directory.
    app = Flask(__name__)
    # from_object() reads every UPPER_CASE attribute of config_class and
    # stores it in app.config (a dict-like object).
    app.config.from_object(config_class)

    # init_app() binds each extension to this specific app instance.  Under
    # the hood each extension stores a reference to the app so it can push
    # an app context when needed (e.g. db knows which DB URI to connect to).
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    # By default Flask-WTF only looks for the CSRF token in form data.
    # HTMX sends it as a custom request header instead, so we add it here.
    app.config["WTF_CSRF_HEADERS"] = ["X-CSRFToken"]

    # Register blueprints
    from app.routes.main import bp as main_bp
    from app.routes.signatures import bp as signatures_bp
    from app.routes.collectors import bp as collectors_bp
    from app.routes.stats import bp as stats_bp
    from app.routes.auth import bp as auth_bp
    from app.routes.settings import bp as settings_bp
    from app.routes.users import bp as users_bp
    from app.routes.organizations import bp as organizations_bp
    from app.routes.imports import bp as imports_bp
    from app.routes.prints import bp as prints_bp
    from app.routes.help import bp as help_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(signatures_bp, url_prefix="/signatures")
    app.register_blueprint(collectors_bp, url_prefix="/collectors")
    app.register_blueprint(stats_bp, url_prefix="/stats")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(settings_bp, url_prefix="/settings")
    app.register_blueprint(users_bp, url_prefix="/users")
    app.register_blueprint(organizations_bp, url_prefix="/organizations")
    app.register_blueprint(imports_bp, url_prefix="/imports")
    app.register_blueprint(prints_bp, url_prefix="/prints")
    app.register_blueprint(help_bp, url_prefix="/help")

    # @app.context_processor registers inject_globals() so that its return
    # dict is automatically merged into every Jinja2 template's variables.
    # Templates can reference {{ app_version }}, {{ branding }}, etc. without
    # any route handler passing them explicitly.
    @app.context_processor
    def inject_globals():
        """
        Inject app-wide template variables on every request.

        Reads branding and font settings from the DB and returns a dict that
        Jinja2 merges into every template's namespace.  Falls back to
        sensible defaults if the settings table doesn't exist yet (e.g.
        during ``flask db upgrade`` before migrations have run).

        Returns:
            dict with keys: ``app_version``, ``branding``, ``branding_palette``
        """
        from app.services.branding import build_palette
        from app.services.fonts import (
            build_font_url, get_headline_css_stack, get_body_css_stack,
            DEFAULT_HEADLINE_FONT, DEFAULT_BODY_FONT,
        )
        try:
            from app.models import Settings
            cfg = Settings.get_branding_config()
            fonts = Settings.get_branding_fonts()
            palette = build_palette(
                cfg["primary_color"] or DEFAULT_PRIMARY,
                cfg["accent_color"] or DEFAULT_ACCENT,
            )
        except Exception:
            # Settings table doesn't exist yet (fresh install / during
            # migrations) — fall back to hardcoded defaults.
            cfg = {
                "mode": "",
                "org_name": "",
                "has_logo": False,
                "logo_mime": "image/png",
                "primary_color": "",
                "accent_color": "",
            }
            fonts = {"headline_font": DEFAULT_HEADLINE_FONT, "body_font": DEFAULT_BODY_FONT}
            palette = build_palette(DEFAULT_PRIMARY, DEFAULT_ACCENT)

        headline_font = fonts["headline_font"]
        body_font = fonts["body_font"]
        cfg["headline_font"] = headline_font
        cfg["body_font"] = body_font
        cfg["font_url"] = build_font_url(headline_font, body_font)
        cfg["headline_font_stack"] = get_headline_css_stack(headline_font)
        cfg["body_font_stack"] = get_body_css_stack(body_font)

        return {"app_version": __version__, "branding": cfg, "branding_palette": palette}

    # @app.before_request registers a function that Flask calls before every
    # request is dispatched to its route handler.  Returning a response from
    # this function short-circuits the normal handler entirely.
    @app.before_request
    def enforce_password_change():
        """
        Before-request hook with two jobs:

        1. Update ``current_user.last_seen`` (throttled to once per minute)
           so the system-health dashboard can show recent activity.
        2. Redirect any user with ``must_change_password=True`` to the
           change-password page, blocking access to all other pages.

        Skips both checks for unauthenticated users and auth/static routes to
        avoid redirect loops.
        """
        from datetime import datetime
        from flask import request, redirect, url_for
        from flask_login import current_user
        # current_user is a Flask-Login proxy that resolves to the logged-in
        # User object, or an AnonymousUser if nobody is logged in.
        if not current_user.is_authenticated:
            return
        # Don't intercept auth routes (login, logout, etc.) or static files,
        # otherwise the forced-password-change redirect would loop forever.
        if request.endpoint and (
            request.endpoint.startswith("auth.") or request.endpoint == "static"
        ):
            return
        # Track last activity for the health dashboard (throttle to once per minute)
        try:
            now = datetime.utcnow()
            if current_user.last_seen is None or (now - current_user.last_seen).total_seconds() > 60:
                current_user.last_seen = now
                db.session.commit()  # Write the updated timestamp to the DB
        except Exception:
            db.session.rollback()  # Roll back to keep the session clean on error
        if current_user.must_change_password:
            # redirect() returns HTTP 302; url_for() resolves the endpoint
            # name to a URL path without hard-coding it.
            return redirect(url_for("auth.change_password"))

    # Runtime startup tasks (run after migrations have been applied)
    with app.app_context():
        from app.models import User, Voter, Signature, Book, Batch, Collector, DataEnterer, Settings, VoterImport, PetitionPrintJob, UserLoginEvent

        # Ensure pg_trgm extension and voter search indexes exist.
        # Wrapped in try/except because this runs during `flask db upgrade`
        # before migrations have created the voters table.
        try:
            from app.services.voter_search import ensure_search_indexes
            ensure_search_indexes()
        except Exception:
            pass

        # Seed the counties lookup table if it's empty (first run).
        # Wrapped in try/except because this runs during `flask db upgrade`
        # before migrations have created the counties table.
        try:
            from app.services.voter_import import VoterImportService
            VoterImportService.ensure_counties()
        except Exception:
            pass

        # Recover imports left in running/pending state from a previous crash.
        # Wrapped in try/except because this runs during `flask db upgrade`
        # before migrations have created the voter_imports table.
        try:
            from app.services.voter_import import VoterImportService
            VoterImportService.recover_stale_imports()
        except Exception:
            pass

    # Start the backup scheduler (reads schedule setting from DB).
    # Wrapped in try/except because this runs during `flask db upgrade`
    # before migrations have created the settings table.
    try:
        from app.services.scheduler import init_app as init_scheduler
        init_scheduler(app)
    except Exception:
        pass

    # Register dev CLI commands (flask dev seed / flask dev wipe).
    from app.dev_commands import dev_cli
    app.cli.add_command(dev_cli)

    return app

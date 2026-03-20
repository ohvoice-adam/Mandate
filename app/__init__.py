__version__ = "0.3.1"

DEFAULT_PRIMARY = "#0c3e6b"
DEFAULT_ACCENT = "#f56708"

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate

from app.config import Config

from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
csrf = CSRFProtect()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    # Accept CSRF token from X-CSRFToken header (used by HTMX requests)
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

    @app.context_processor
    def inject_globals():
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

    @app.before_request
    def enforce_password_change():
        from datetime import datetime
        from flask import request, redirect, url_for
        from flask_login import current_user
        if not current_user.is_authenticated:
            return
        if request.endpoint and (
            request.endpoint.startswith("auth.") or request.endpoint == "static"
        ):
            return
        # Track last activity for the health dashboard (throttle to once per minute)
        try:
            now = datetime.utcnow()
            if current_user.last_seen is None or (now - current_user.last_seen).total_seconds() > 60:
                current_user.last_seen = now
                db.session.commit()
        except Exception:
            db.session.rollback()
        if current_user.must_change_password:
            return redirect(url_for("auth.change_password"))

    # Runtime startup tasks (run after migrations have been applied)
    with app.app_context():
        from app.models import User, Voter, Signature, Book, Batch, Collector, DataEnterer, Settings, VoterImport, PetitionPrintJob

        # Ensure pg_trgm extension and voter search indexes exist.
        # Wrapped in try/except because this runs during `flask db upgrade`
        # before migrations have created the voters table.
        try:
            from app.services.voter_search import ensure_search_indexes
            ensure_search_indexes()
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

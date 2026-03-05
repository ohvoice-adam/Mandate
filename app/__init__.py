__version__ = "0.1.4"

DEFAULT_PRIMARY = "#0c3e6b"
DEFAULT_ACCENT = "#f56708"

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate

from app.config import Config

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = "auth.login"


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

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

    app.register_blueprint(main_bp)
    app.register_blueprint(signatures_bp, url_prefix="/signatures")
    app.register_blueprint(collectors_bp, url_prefix="/collectors")
    app.register_blueprint(stats_bp, url_prefix="/stats")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(settings_bp, url_prefix="/settings")
    app.register_blueprint(users_bp, url_prefix="/users")
    app.register_blueprint(organizations_bp, url_prefix="/organizations")
    app.register_blueprint(imports_bp, url_prefix="/imports")

    @app.context_processor
    def inject_globals():
        from app.services.branding import build_palette
        try:
            from app.models import Settings
            cfg = Settings.get_branding_config()
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
            palette = build_palette(DEFAULT_PRIMARY, DEFAULT_ACCENT)
        return {"app_version": __version__, "branding": cfg, "branding_palette": palette}

    @app.before_request
    def enforce_password_change():
        from flask import request, redirect, url_for
        from flask_login import current_user
        if not current_user.is_authenticated:
            return
        if request.endpoint and (
            request.endpoint.startswith("auth.") or request.endpoint == "static"
        ):
            return
        if current_user.must_change_password:
            return redirect(url_for("auth.change_password"))

    # Runtime startup tasks (run after migrations have been applied)
    with app.app_context():
        from app.models import User, Voter, Signature, Book, Batch, Collector, DataEnterer, Settings, VoterImport

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

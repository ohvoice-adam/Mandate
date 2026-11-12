"""System health dashboard."""

from datetime import datetime, timedelta
from typing import Any

from flask import render_template
from flask.typing import ResponseReturnValue
from flask_login import login_required
from sqlalchemy import bindparam, text

from app import db
from app.models import ImportStatus, Settings, VoterImport, admin_required
from app.routes.settings import bp
from app.services import backup as backup_service

_ACTIVE_WINDOW = timedelta(minutes=30)


def _active_users(threshold: datetime) -> list[Any]:
    """Users seen since *threshold*, with their last-entered signature time."""
    return db.session.execute(text("""
        SELECT
            u.id,
            u.first_name,
            u.last_name,
            u.email,
            u.role,
            u.last_seen,
            MAX(s.created_at) AS last_signature_at
        FROM users u
        LEFT JOIN batches b  ON b.enterer_id = u.id
        LEFT JOIN signatures s ON s.batch_id = b.id
        WHERE u.last_seen >= :threshold
        GROUP BY u.id, u.first_name, u.last_name, u.email, u.role, u.last_seen
        ORDER BY u.last_seen DESC
    """), {"threshold": threshold}).fetchall()


def _counts(city_aliases: list[str]) -> Any:
    """Headline record counts for the dashboard."""
    return db.session.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM users WHERE is_active = TRUE)          AS total_users,
            (SELECT COUNT(*) FROM signatures)                            AS total_signatures,
            (SELECT COUNT(*) FROM voters
             WHERE city IN :city_aliases)                                AS total_voters,
            (SELECT COUNT(*) FROM books)                                 AS total_books,
            (SELECT COUNT(*) FROM batches WHERE status = 'open')         AS open_batches,
            (SELECT COUNT(DISTINCT county_number) FROM voters
             WHERE county_number IS NOT NULL AND county_number <> '')    AS loaded_counties
    """).bindparams(bindparam("city_aliases", expanding=True)), {"city_aliases": city_aliases}).fetchone()


def _hourly_activity() -> list[Any]:
    """Signature counts per hour over the last 24 hours."""
    return db.session.execute(text("""
        SELECT
            date_trunc('hour', created_at) AS hour,
            COUNT(*) AS count
        FROM signatures
        WHERE created_at >= NOW() - INTERVAL '24 hours'
        GROUP BY 1
        ORDER BY 1
    """)).fetchall()


def _login_history() -> list[Any]:
    """Most recent 100 logins from the last 7 days."""
    return db.session.execute(text("""
        SELECT
            e.logged_in_at,
            e.ip_address,
            u.first_name,
            u.last_name,
            u.email,
            u.role
        FROM user_login_events e
        JOIN users u ON u.id = e.user_id
        WHERE e.logged_in_at >= NOW() - INTERVAL '7 days'
        ORDER BY e.logged_in_at DESC
        LIMIT 100
    """)).fetchall()


def _last_completed_import() -> VoterImport | None:
    """Most recent completed voter import, if any."""
    return (
        VoterImport.query
        .filter_by(status=ImportStatus.COMPLETED)
        .order_by(VoterImport.completed_at.desc())
        .first()
    )


@bp.route("/system-health")
@login_required
@admin_required
def system_health() -> ResponseReturnValue:
    """System health dashboard."""
    now = datetime.utcnow()
    return render_template(
        "settings/system_health.html",
        active_users=_active_users(now - _ACTIVE_WINDOW),
        counts=_counts(Settings.get_city_aliases()),
        last_import=_last_completed_import(),
        backup_config=Settings.get_backup_config(),
        backup_configured=backup_service.is_configured(),
        hourly_activity=_hourly_activity(),
        login_history=_login_history(),
        target_city_display=Settings.get_target_city_display(),
        now=now,
    )

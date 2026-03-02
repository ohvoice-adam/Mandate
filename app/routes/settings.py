from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
from flask_login import login_required
from sqlalchemy import text

from app import db
from app.models import Settings, admin_required
from app.services import backup as backup_service
from app.services import scheduler as scheduler_service
from app.services import email as email_service
from app.utils import is_valid_email

bp = Blueprint("settings", __name__)


@bp.route("/", methods=["GET", "POST"])
@login_required
@admin_required
def index():
    """Application settings page."""
    if request.method == "POST":
        target_city = request.form.get("target_city")
        if target_city:
            Settings.set("target_city", target_city.upper())

        signature_goal = request.form.get("signature_goal", "").strip()
        if signature_goal:
            try:
                goal = int(signature_goal)
                if goal >= 0:
                    Settings.set_signature_goal(goal)
            except ValueError:
                flash("Signature goal must be a number", "error")
                return redirect(url_for("settings.index"))

        flash("Settings saved", "success")
        return redirect(url_for("settings.index"))

    current_city = Settings.get_target_city()
    signature_goal = Settings.get_signature_goal()
    cities = get_distinct_cities()
    backup_config = Settings.get_backup_config()
    backup_configured = backup_service.is_configured()
    smtp_config = Settings.get_smtp_config()
    smtp_configured = email_service.is_configured()
    notify_config = Settings.get_backup_notify_config()

    return render_template(
        "settings/index.html",
        current_city=current_city,
        cities=cities,
        signature_goal=signature_goal,
        backup_config=backup_config,
        backup_configured=backup_configured,
        smtp_config=smtp_config,
        smtp_configured=smtp_configured,
        notify_config=notify_config,
    )


@bp.route("/save-backup-config", methods=["POST"])
@login_required
@admin_required
def save_backup_config():
    """Save SCP backup configuration."""
    key_content = None
    key_file = request.files.get("scp_key_file")
    if key_file and key_file.filename:
        try:
            key_content = key_file.read().decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            flash("Invalid key file — must be a text-format PEM private key.", "error")
            return redirect(url_for("settings.index"))
        if not key_content.strip():
            key_content = None

    Settings.save_backup_config(
        host=request.form.get("scp_host", ""),
        port=request.form.get("scp_port", "22"),
        user=request.form.get("scp_user", ""),
        remote_path=request.form.get("scp_remote_path", ""),
        key_content=key_content,
    )

    schedule = request.form.get("backup_schedule", "")
    if schedule not in ("", "hourly", "daily", "weekly"):
        schedule = ""
    Settings.set("backup_schedule", schedule)
    scheduler_service.apply_schedule(current_app._get_current_object())

    notify_email = request.form.get("backup_notify_email", "").strip()
    notify_success = request.form.get("backup_notify_success", "")
    if notify_success not in ("", "each", "daily", "weekly"):
        notify_success = ""
    notify_failure = "true" if request.form.get("backup_notify_failure") else "false"
    Settings.save_backup_notify_config(
        notify_email=notify_email,
        notify_success=notify_success,
        notify_failure=notify_failure,
    )

    flash("Backup configuration saved", "success")
    return redirect(url_for("settings.index"))


@bp.route("/test-backup-connection", methods=["POST"])
@login_required
@admin_required
def test_backup_connection():
    """Test the SFTP connection and return JSON {ok, message}."""
    try:
        if not backup_service.is_configured():
            return jsonify(ok=False, message="Backup is not fully configured.")

        scp_config = {
            "host": Settings.get("backup_scp_host"),
            "port": int(Settings.get("backup_scp_port", "22") or "22"),
            "user": Settings.get("backup_scp_user"),
            "key_content": Settings.get("backup_scp_key_content"),
        }
        password = request.form.get("test_password") or None
        ok, message = backup_service.test_sftp_connection(scp_config, password=password)
        return jsonify(ok=ok, message=message)
    except Exception as exc:
        current_app.logger.exception("Unexpected error in test_backup_connection")
        return jsonify(ok=False, message=f"Server error: {exc}"), 500


@bp.route("/run-backup", methods=["POST"])
@login_required
@admin_required
def run_backup():
    """Trigger an asynchronous database backup."""
    try:
        backup_service.run_backup_async(current_app._get_current_object())
        flash("Backup started. Check status below.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("settings.index"))


@bp.route("/save-smtp-config", methods=["POST"])
@login_required
@admin_required
def save_smtp_config():
    """Save SMTP email configuration."""
    from_email = request.form.get("smtp_from_email", "").strip()
    if from_email and not is_valid_email(from_email):
        flash("Invalid From Address email.", "error")
        return redirect(url_for("settings.index"))

    Settings.save_smtp_config(
        host=request.form.get("smtp_host", ""),
        port=request.form.get("smtp_port", "587"),
        user=request.form.get("smtp_user", ""),
        from_email=from_email,
        use_tls=bool(request.form.get("smtp_use_tls")),
        password=request.form.get("smtp_password") or None,
    )
    flash("Email configuration saved", "success")
    return redirect(url_for("settings.index"))


@bp.route("/test-smtp", methods=["POST"])
@login_required
@admin_required
def test_smtp():
    """Send a test email and return JSON {ok, message}."""
    from flask_login import current_user

    try:
        if not email_service.is_configured():
            return jsonify(ok=False, message="Email is not fully configured.")
        email_service.send_email(
            to=current_user.email,
            subject="Mandate — SMTP Test",
            body_html="<p>SMTP is configured correctly.</p>",
            body_text="SMTP is configured correctly.",
        )
        return jsonify(ok=True, message=f"Test email sent to {current_user.email}.")
    except Exception as exc:
        current_app.logger.exception("SMTP test failed")
        return jsonify(ok=False, message=f"Error: {exc}"), 500


def get_distinct_cities() -> list[dict]:
    """Get distinct cities from the voter file."""
    result = db.session.execute(text("""
        SELECT DISTINCT city, COUNT(*) as count
        FROM voters
        WHERE city IS NOT NULL AND city != ''
        GROUP BY city
        ORDER BY count DESC
    """))

    cities = []
    for row in result:
        cities.append({
            "value": row.city,
            "label": row.city.title(),
            "count": row.count,
        })

    return cities

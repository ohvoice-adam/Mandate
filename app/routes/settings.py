import base64
import os

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify, abort, Response, send_file, after_this_request
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

        allow_pdf_deletion = "true" if request.form.get("allow_pdf_deletion") else "false"
        Settings.set("allow_pdf_deletion", allow_pdf_deletion)

        flash("Settings saved", "success")
        return redirect(url_for("settings.index"))

    current_city = Settings.get_target_city()
    signature_goal = Settings.get_signature_goal()
    allow_pdf_deletion = Settings.get("allow_pdf_deletion", "false") == "true"
    cities = get_distinct_cities()
    backup_config = Settings.get_backup_config()
    backup_configured = backup_service.is_configured()
    smtp_config = Settings.get_smtp_config()
    smtp_configured = email_service.is_configured()
    notify_config = Settings.get_backup_notify_config()
    branding_config = Settings.get_branding_config()
    branding_fonts = Settings.get_branding_fonts()
    from app.services.fonts import HEADLINE_FONTS, BODY_FONTS
    headline_font_options = [(name, name) for name, _, _ in HEADLINE_FONTS]
    body_font_options = [(name, name) for name, _, _ in BODY_FONTS]
    font_specs = {name: spec for name, spec, _ in HEADLINE_FONTS + BODY_FONTS}

    return render_template(
        "settings/index.html",
        current_city=current_city,
        cities=cities,
        signature_goal=signature_goal,
        allow_pdf_deletion=allow_pdf_deletion,
        backup_config=backup_config,
        backup_configured=backup_configured,
        smtp_config=smtp_config,
        smtp_configured=smtp_configured,
        notify_config=notify_config,
        branding_config=branding_config,
        branding_fonts=branding_fonts,
        headline_font_options=headline_font_options,
        body_font_options=body_font_options,
        font_specs=font_specs,
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
    except Exception:
        current_app.logger.exception("Unexpected error in test_backup_connection")
        return jsonify(ok=False, message="Server error. Check the application logs."), 500


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


@bp.route("/branding-logo")
@login_required
def branding_logo():
    """Serve the stored org logo."""
    data = Settings.get_logo_bytes()
    if not data:
        abort(404)
    mime = Settings.get("branding_logo_mime", "image/png")
    return Response(data, mimetype=mime)


@bp.route("/clear-logo", methods=["POST"])
@login_required
@admin_required
def clear_logo():
    """Remove the stored org logo."""
    Settings.set("branding_logo_content", "")
    Settings.set("branding_logo_mime", "")
    flash("Logo removed", "success")
    return redirect(url_for("settings.index"))


@bp.route("/save-branding-config", methods=["POST"])
@login_required
@admin_required
def save_branding_config():
    """Save branding configuration."""
    mode = request.form.get("branding_mode", "")
    if mode not in ("", "dual", "white-label"):
        mode = ""
    org_name = request.form.get("branding_org_name", "").strip()

    # Logo upload (optional)
    logo_file = request.files.get("branding_logo")
    if logo_file and logo_file.filename:
        logo_bytes = logo_file.read()
        # Validate actual image content with Pillow — never trust the client MIME type
        try:
            from PIL import Image
            import io as _io
            img = Image.open(_io.BytesIO(logo_bytes))
            img.verify()
            fmt = (img.format or "").lower()
            allowed_formats = {"png", "jpeg", "gif", "webp"}
            if fmt not in allowed_formats:
                flash(f"Logo must be a PNG, JPEG, GIF, or WebP image (got {fmt or 'unknown'}).", "error")
                return redirect(url_for("settings.index"))
            mime = f"image/{fmt}" if fmt != "jpeg" else "image/jpeg"
        except Exception:
            flash("Uploaded logo is not a valid image file.", "error")
            return redirect(url_for("settings.index"))
        Settings.set("branding_logo_content", base64.b64encode(logo_bytes).decode())
        Settings.set("branding_logo_mime", mime)
        # Auto-extracted colors always win when a new logo is uploaded.
        # color inputs are always non-empty (browser default), so we can't
        # distinguish "user typed a value" from "pre-filled default" here.
        from app.services.branding import extract_colors_from_image
        final_primary, final_accent = extract_colors_from_image(logo_bytes)
    else:
        # No new logo — honor whatever the color pickers submitted.
        final_primary = request.form.get("branding_primary_color", "").strip()
        final_accent = request.form.get("branding_accent_color", "").strip()

    # Switching back to default Mandate branding clears stored colors so the
    # context processor falls back to the hardcoded navy/orange defaults.
    if mode == "":
        final_primary = ""
        final_accent = ""

    Settings.save_branding_config(mode, org_name, final_primary, final_accent)

    from app.services.fonts import HEADLINE_FONTS, BODY_FONTS, DEFAULT_HEADLINE_FONT, DEFAULT_BODY_FONT
    headline_names = {name for name, _, _ in HEADLINE_FONTS}
    body_names = {name for name, _, _ in BODY_FONTS}
    headline_font = request.form.get("branding_headline_font", "").strip()
    body_font = request.form.get("branding_body_font", "").strip()
    if headline_font not in headline_names:
        headline_font = DEFAULT_HEADLINE_FONT
    if body_font not in body_names:
        body_font = DEFAULT_BODY_FONT
    Settings.save_branding_fonts(headline_font, body_font)

    flash("Branding configuration saved", "success")
    return redirect(url_for("settings.index"))


@bp.route("/download-backup")
@login_required
@admin_required
def download_backup():
    """Stream a pg_dump of the database as a direct browser download."""
    from datetime import datetime

    try:
        dump_path = backup_service.create_local_dump()
    except RuntimeError as exc:
        flash(str(exc), "error")
        return redirect(url_for("settings.index"))

    @after_this_request
    def _cleanup(response):
        try:
            os.unlink(dump_path)
        except OSError:
            pass
        return response

    filename = f"mandate-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.dump"
    return send_file(
        dump_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/octet-stream",
    )


@bp.route("/export-config")
@login_required
@admin_required
def export_config():
    """Download non-sensitive settings as a JSON file."""
    import json
    from datetime import datetime

    SENSITIVE = {"backup_scp_key_content", "smtp_password", "branding_logo_content"}

    rows = Settings.query.order_by(Settings.key).all()
    data = {row.key: row.value for row in rows if row.key not in SENSITIVE}

    payload = json.dumps({"mandate_settings": data, "exported_at": datetime.utcnow().isoformat()}, indent=2)
    filename = f"mandate-config-{datetime.utcnow().strftime('%Y%m%d')}.json"
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/import-config", methods=["POST"])
@login_required
@admin_required
def import_config():
    """Restore non-sensitive settings from an uploaded JSON file."""
    import json

    SENSITIVE = {"backup_scp_key_content", "smtp_password", "branding_logo_content"}

    uploaded = request.files.get("config_file")
    if not uploaded or not uploaded.filename:
        flash("No file selected.", "error")
        return redirect(url_for("settings.index"))

    try:
        raw = uploaded.read().decode("utf-8")
        obj = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        flash("Invalid file — must be a UTF-8 encoded JSON file.", "error")
        return redirect(url_for("settings.index"))

    data = obj.get("mandate_settings") if isinstance(obj, dict) else None
    if not isinstance(data, dict):
        flash("Invalid config file format.", "error")
        return redirect(url_for("settings.index"))

    imported = 0
    for key, value in data.items():
        if key in SENSITIVE:
            continue
        if not isinstance(key, str) or not isinstance(value, (str, type(None))):
            continue
        Settings.set(key, value or "")
        imported += 1

    scheduler_service.apply_schedule(current_app._get_current_object())
    flash(f"Configuration imported ({imported} settings restored).", "success")
    return redirect(url_for("settings.index"))


@bp.route("/system-health")
@login_required
@admin_required
def system_health():
    """System health dashboard."""
    from datetime import datetime, timedelta
    from sqlalchemy import text as sqlt
    from app.models import User, VoterImport, ImportStatus

    now = datetime.utcnow()
    active_threshold = now - timedelta(minutes=30)

    # Users active in the last 30 minutes, with their last-entered signature timestamp
    active_users_rows = db.session.execute(sqlt("""
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
    """), {"threshold": active_threshold}).fetchall()

    city_pattern = Settings.get_target_city_pattern()
    target_city_display = Settings.get_target_city_display()

    # Counts
    counts = db.session.execute(sqlt("""
        SELECT
            (SELECT COUNT(*) FROM users WHERE is_active = TRUE)          AS total_users,
            (SELECT COUNT(*) FROM signatures)                            AS total_signatures,
            (SELECT COUNT(*) FROM voters
             WHERE city LIKE :city_pattern)                              AS total_voters,
            (SELECT COUNT(*) FROM books)                                 AS total_books,
            (SELECT COUNT(*) FROM batches WHERE status = 'open')         AS open_batches,
            (SELECT COUNT(DISTINCT county_number) FROM voters
             WHERE county_number IS NOT NULL AND county_number <> '')    AS loaded_counties
    """), {"city_pattern": city_pattern}).fetchone()

    # Most recent completed voter import
    last_import = (
        VoterImport.query
        .filter_by(status=ImportStatus.COMPLETED)
        .order_by(VoterImport.completed_at.desc())
        .first()
    )

    # Backup info
    backup_config = Settings.get_backup_config()

    # Recent signature activity (last 24h, by hour)
    hourly_activity = db.session.execute(sqlt("""
        SELECT
            date_trunc('hour', created_at) AS hour,
            COUNT(*) AS count
        FROM signatures
        WHERE created_at >= NOW() - INTERVAL '24 hours'
        GROUP BY 1
        ORDER BY 1
    """)).fetchall()

    # Login history (last 7 days, most recent 100)
    login_history = db.session.execute(sqlt("""
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

    return render_template(
        "settings/system_health.html",
        active_users=active_users_rows,
        counts=counts,
        last_import=last_import,
        backup_config=backup_config,
        hourly_activity=hourly_activity,
        login_history=login_history,
        target_city_display=target_city_display,
        now=now,
    )


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

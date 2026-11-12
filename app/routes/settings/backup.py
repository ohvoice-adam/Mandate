"""Backup routes: destination config, connection tests, run and download."""

import os
from datetime import datetime

from flask import (
    Response,
    after_this_request,
    current_app,
    flash,
    jsonify,
    redirect,
    request,
    send_file,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_login import login_required

from app.models import Settings, admin_required
from app.routes.settings import bp
from app.services import backup as backup_service
from app.services import backup_gdrive as gdrive_service
from app.services import scheduler as scheduler_service


def _read_scp_key_form() -> tuple[str | None, str | None]:
    """Return (key_content_or_None, error_or_None) from the SSH key upload.

    None key content means no new key was uploaded, so the stored one is kept.
    """
    key_file = request.files.get("scp_key_file")
    if not key_file or not key_file.filename:
        return None, None
    try:
        key_content = key_file.read().decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None, "Invalid key file — must be a text-format PEM private key."
    return (key_content if key_content.strip() else None), None


def _read_gdrive_form() -> tuple[str | None, str, str | None]:
    """Parse the Google Drive part of the backup form.

    Returns (sa_json_or_None, folder_id, error_message_or_None).  sa_json is
    None when no new key file was uploaded, so the stored key is kept.
    """
    sa_json = None
    key_file = request.files.get("gdrive_key_file")
    if key_file and key_file.filename:
        try:
            raw = key_file.read().decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None, "", "Invalid Google key file — must be a JSON service-account key."
        if raw.strip():
            try:
                gdrive_service.parse_service_account(raw)
            except ValueError as exc:
                return None, "", f"Google key file rejected: {exc}"
            sa_json = raw

    raw_folder = request.form.get("gdrive_folder_id", "").strip()
    folder_id = gdrive_service.normalize_folder_id(raw_folder)
    if raw_folder and not folder_id:
        return None, "", "Google Drive folder must be a folder ID or a drive.google.com folder link."
    return sa_json, folder_id, None


def _save_schedule_form() -> None:
    """Persist the backup schedule and re-apply it to the scheduler."""
    schedule = request.form.get("backup_schedule", "")
    if schedule not in ("", "hourly", "daily", "weekly"):
        schedule = ""
    Settings.set("backup_schedule", schedule)
    scheduler_service.apply_schedule(current_app._get_current_object())


def _save_backup_notify_form() -> None:
    """Persist the notification fields of the backup form."""
    notify_success = request.form.get("backup_notify_success", "")
    if notify_success not in ("", "each", "daily", "weekly"):
        notify_success = ""
    Settings.save_backup_notify_config(
        notify_email=request.form.get("backup_notify_email", "").strip(),
        notify_success=notify_success,
        notify_failure="true" if request.form.get("backup_notify_failure") else "false",
    )


def _checkbox(name: str) -> str:
    """Return "true"/"false" for a checkbox field."""
    return "true" if request.form.get(name) else "false"


@bp.route("/save-backup-config", methods=["POST"])
@login_required
@admin_required
def save_backup_config() -> ResponseReturnValue:
    """Save backup destinations, schedule and notification settings."""
    key_content, key_error = _read_scp_key_form()
    gdrive_sa_json, gdrive_folder_id, gdrive_error = _read_gdrive_form()
    error = key_error or gdrive_error
    if error:
        flash(error, "error")
        return redirect(url_for("settings.index"))

    Settings.save_backup_config(
        host=request.form.get("scp_host", ""),
        port=request.form.get("scp_port", "22"),
        user=request.form.get("scp_user", ""),
        remote_path=request.form.get("scp_remote_path", ""),
        key_content=key_content,
        enable_remote=_checkbox("backup_enable_remote"),
        enable_local=_checkbox("backup_enable_local"),
        local_path=request.form.get("backup_local_path", "").strip(),
        enable_gdrive=_checkbox("backup_enable_gdrive"),
        gdrive_folder_id=gdrive_folder_id,
        gdrive_sa_json=gdrive_sa_json,
    )
    Settings.set("site_url", request.form.get("site_url", "").strip())
    _save_schedule_form()
    _save_backup_notify_form()

    flash("Backup configuration saved", "success")
    return redirect(url_for("settings.index"))


@bp.route("/test-backup-connection", methods=["POST"])
@login_required
@admin_required
def test_backup_connection() -> ResponseReturnValue:
    """Test the SFTP connection and return JSON {ok, message}."""
    try:
        if not backup_service.is_remote_configured():
            return jsonify(ok=False, message="SFTP backup is not fully configured.")

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


@bp.route("/test-gdrive-connection", methods=["POST"])
@login_required
@admin_required
def test_gdrive_connection() -> ResponseReturnValue:
    """Test the Google Drive key + folder and return JSON {ok, message}."""
    try:
        sa_json = Settings.get("backup_gdrive_sa_json")
        folder_id = (Settings.get("backup_gdrive_folder_id") or "").strip()
        if not sa_json or not folder_id:
            return jsonify(ok=False, message="Upload a service-account key and set a folder ID, then save.")
        ok, message = gdrive_service.test_connection(sa_json, folder_id)
        return jsonify(ok=ok, message=message)
    except Exception:
        current_app.logger.exception("Unexpected error in test_gdrive_connection")
        return jsonify(ok=False, message="Server error. Check the application logs."), 500


@bp.route("/run-backup", methods=["POST"])
@login_required
@admin_required
def run_backup() -> ResponseReturnValue:
    """Trigger an asynchronous database backup."""
    try:
        backup_service.run_backup_async(current_app._get_current_object())
        flash("Backup started. Check status below.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("settings.index"))


@bp.route("/download-backup")
@login_required
@admin_required
def download_backup() -> ResponseReturnValue:
    """Stream a pg_dump of the database as a direct browser download."""
    try:
        dump_path = backup_service.create_local_dump()
    except RuntimeError as exc:
        flash(str(exc), "error")
        return redirect(url_for("settings.index"))

    # after_this_request() registers a callback that runs after Flask finishes
    # streaming the response.  We can't delete the temp file before send_file()
    # returns because Flask would still be reading from it.
    @after_this_request
    def _cleanup(response: Response) -> Response:
        try:
            os.unlink(dump_path)
        except OSError:
            current_app.logger.warning("Could not delete temp dump %s", dump_path)
        return response

    filename = f"mandate-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.dump"
    return send_file(
        dump_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/octet-stream",
    )

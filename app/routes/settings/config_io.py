"""Export and import of non-sensitive settings as JSON."""

import json
from datetime import datetime

from flask import Response, current_app, flash, redirect, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from app.models import Settings, admin_required
from app.routes.settings import bp
from app.services import scheduler as scheduler_service

# Never leave (or enter) via config export/import: secrets, large blobs, and
# the backup-name history, which belongs to this instance's backup folders
# (importing another campaign's would let retention manage its files).
SENSITIVE_KEYS = frozenset({
    "backup_scp_key_content",
    "backup_gdrive_sa_json",
    "smtp_password",
    "branding_logo_content",
    "backup_name_prefixes",
})


@bp.route("/export-config")
@login_required
@admin_required
def export_config() -> ResponseReturnValue:
    """Download non-sensitive settings as a JSON file."""
    rows = Settings.query.order_by(Settings.key).all()
    data = {row.key: row.value for row in rows if row.key not in SENSITIVE_KEYS}

    payload = json.dumps({"mandate_settings": data, "exported_at": datetime.utcnow().isoformat()}, indent=2)
    filename = f"mandate-config-{datetime.utcnow().strftime('%Y%m%d')}.json"
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _apply_imported_settings(data: dict) -> int:
    """Store every safe string/None value from *data*; return how many were set."""
    imported = 0
    for key, value in data.items():
        if key in SENSITIVE_KEYS:
            continue
        if not isinstance(key, str) or not isinstance(value, (str, type(None))):
            continue
        Settings.set(key, value or "")
        imported += 1
    return imported


@bp.route("/import-config", methods=["POST"])
@login_required
@admin_required
def import_config() -> ResponseReturnValue:
    """Restore non-sensitive settings from an uploaded JSON file."""
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

    imported = _apply_imported_settings(data)
    scheduler_service.apply_schedule(current_app._get_current_object())
    flash(f"Configuration imported ({imported} settings restored).", "success")
    return redirect(url_for("settings.index"))

"""Database backup orchestration: pg_dump, then send to each enabled destination.

Destinations live in their own modules:
  - SFTP:          app/services/backup_sftp.py
  - Local dir:     app/services/backup_retention.py (local_save)
  - Google Drive:  app/services/backup_gdrive.py
Retention rules shared by all of them are in app/services/backup_retention.py.
"""

import logging
import os
import threading
from datetime import datetime

from flask import Flask

from app.services import backup_retention, backup_sftp
from app.services.backup_dump import (
    BACKUP_TABLES,  # noqa: F401  (public constant)
    server_major_version,
)
from app.services.backup_dump import create_pg_dump as _create_pg_dump

# Re-exported for callers that use backup_service.test_sftp_connection.
from app.services.backup_sftp import test_sftp_connection  # noqa: F401

logger = logging.getLogger(__name__)

# Stable bigint used as a PostgreSQL advisory lock key to prevent concurrent
# backup runs across multiple Gunicorn workers.  Value is ASCII "MAND".
_ADVISORY_LOCK_KEY = 0x4D414E44


def create_local_dump() -> str:
    """Run pg_dump of BACKUP_TABLES and return the path to the temp file.

    Caller is responsible for deleting the file after use.
    Raises RuntimeError on failure.
    """
    from flask import current_app

    db_url = os.environ.get("DATABASE_URL") or current_app.config.get(
        "SQLALCHEMY_DATABASE_URI", ""
    )
    return _create_pg_dump(db_url, server_major_version())


def is_remote_configured() -> bool:
    """Return True if SFTP backup is enabled and all SCP settings are present."""
    from app.models import Settings

    # Default "true" preserves legacy behaviour: pre-existing installs without this key
    # still attempt remote backup (gated by SCP credential checks below).
    if Settings.get("backup_enable_remote", "true") != "true":
        return False
    return all(
        Settings.get(k)
        for k in (
            "backup_scp_host",
            "backup_scp_user",
            "backup_scp_key_content",
            "backup_scp_remote_path",
        )
    )


def is_local_configured() -> bool:
    """Return True if local backup is enabled and a local path is configured."""
    from app.models import Settings

    if Settings.get("backup_enable_local", "false") != "true":
        return False
    return bool(Settings.get("backup_local_path", "").strip())


def is_gdrive_configured() -> bool:
    """Return True if Google Drive backup is enabled with a key and folder ID."""
    from app.models import Settings

    if Settings.get("backup_enable_gdrive", "false") != "true":
        return False
    return bool(Settings.get("backup_gdrive_sa_json")) and bool(
        (Settings.get("backup_gdrive_folder_id") or "").strip()
    )


def is_configured() -> bool:
    """Return True if at least one backup destination is enabled and configured."""
    return is_remote_configured() or is_local_configured() or is_gdrive_configured()


def run_backup_async(app: Flask) -> None:
    """Kick off a backup in a background daemon thread.

    Sets status to 'running' synchronously before returning so the caller
    can immediately reflect that state in the UI.

    Raises ValueError if backup is not configured or already running.
    """
    from app.models import Settings

    if not is_configured():
        raise ValueError("Backup is not fully configured.")

    current_status = Settings.get("backup_last_status", "")
    if current_status == "running":
        raise ValueError("A backup is already in progress.")

    Settings.set("backup_last_status", "running")
    Settings.set("backup_last_run", datetime.now().isoformat())

    thread = threading.Thread(target=_backup_thread, args=(app,), daemon=True)
    thread.start()


def _backup_thread(app: Flask) -> None:
    """Background thread: dump the database and send to each configured destination."""
    with app.app_context():
        from app.models import Settings

        db_url = os.environ.get("DATABASE_URL") or app.config.get(
            "SQLALCHEMY_DATABASE_URI", ""
        )
        dump_file = None
        try:
            # Matching the server's major version avoids pg_dump
            # "server version mismatch" errors.
            dump_file = _create_pg_dump(db_url, server_major_version())
            errors = _send_to_destinations(dump_file, Settings.get("backup_schedule", ""))
            _record_backup_result(errors)

        except Exception as exc:
            logger.exception("Backup failed")
            # Truncate long error messages to fit in the settings value column
            Settings.set("backup_last_status", f"error:{str(exc)[:300]}")
            try:
                _send_backup_notification(success=False, error_msg=str(exc)[:300])
            except Exception:
                logger.exception("Backup notification failed (backup also failed)")
        finally:
            if dump_file and os.path.exists(dump_file):
                try:
                    os.unlink(dump_file)
                except OSError:
                    pass


def _send_to_destinations(dump_file: str, schedule: str) -> list[str]:
    """Send *dump_file* to every enabled destination; return per-destination errors.

    Each destination is attempted independently so one failure never blocks
    the others.
    """
    from app.models import Settings

    scp_config = {
        "host": Settings.get("backup_scp_host"),
        "port": int(Settings.get("backup_scp_port", "22") or "22"),
        "user": Settings.get("backup_scp_user"),
        "key_content": Settings.get("backup_scp_key_content"),
        "remote_path": Settings.get("backup_scp_remote_path"),
    }
    local_path = (Settings.get("backup_local_path") or "").strip()
    gdrive_key = Settings.get("backup_gdrive_sa_json") or ""
    gdrive_folder = (Settings.get("backup_gdrive_folder_id") or "").strip()

    destinations = [
        ("remote", "Remote", is_remote_configured(),
         lambda: _sftp_upload(dump_file, scp_config, schedule=schedule)),
        ("local", "Local", is_local_configured(),
         lambda: _local_save(dump_file, local_path, schedule=schedule)),
        ("gdrive", "Google Drive", is_gdrive_configured(),
         lambda: _gdrive_upload(dump_file, gdrive_key, gdrive_folder, schedule=schedule)),
    ]
    errors: list[str] = []
    for key, label, enabled, send in destinations:
        if not enabled:
            continue
        try:
            send()
        except Exception as exc:
            logger.exception("%s backup failed", label)
            errors.append(f"{key}: {str(exc)[:200]}")
    return errors


def _record_backup_result(errors: list[str]) -> None:
    """Store the backup status and send the matching notification."""
    from app.models import Settings

    if errors:
        Settings.set("backup_last_status", f"error:{'; '.join(errors)}")
        try:
            _send_backup_notification(success=False, error_msg="; ".join(errors))
        except Exception:
            logger.exception("Backup notification failed (backup also failed)")
    else:
        Settings.set("backup_last_status", "success")
        try:
            _send_backup_notification(success=True, error_msg=None)
        except Exception:
            logger.exception("Backup notification failed (backup itself succeeded)")


def _sftp_upload(local_path: str, scp_config: dict, schedule: str = "") -> None:
    """Upload via SFTP (thin wrapper so tests can patch it here)."""
    backup_sftp.sftp_upload(local_path, scp_config, schedule=schedule)


def _local_save(dump_path: str, local_dir: str, schedule: str = "") -> None:
    """Copy to the local backup directory (thin wrapper so tests can patch it here)."""
    backup_retention.local_save(dump_path, local_dir, schedule=schedule)


def _gdrive_upload(dump_path: str, sa_json: str, folder_id: str, schedule: str = "") -> None:
    """Upload to Google Drive (thin wrapper so tests can patch it here)."""
    from app.services import backup_gdrive

    backup_gdrive.upload(dump_path, sa_json, folder_id, schedule=schedule)


def _send_backup_notification(success: bool, error_msg: str | None) -> None:
    """Send an email notification for a backup success or failure."""
    from app.models import Settings
    from app.services import email as email_service

    notify_email = Settings.get("backup_notify_email", "").strip()
    if not notify_email or not email_service.is_configured():
        return

    email_ctx = Settings.get_email_context()
    backup_time = Settings.get("backup_last_run", "")
    if success:
        mode = Settings.get("backup_notify_success", "")
        if mode == "each":
            email_service.send_backup_success_email(notify_email, backup_time, email_ctx)
        elif mode in ("daily", "weekly"):
            Settings.add_digest_pending(backup_time)
    else:
        if Settings.get("backup_notify_failure", "false") == "true":
            email_service.send_backup_failure_email(notify_email, error_msg or "", backup_time, email_ctx)


def run_backup_sync(app: Flask) -> None:
    """Run a full backup synchronously (for use by the scheduler)."""
    with app.app_context():
        from sqlalchemy import text

        from app import db
        from app.models import Settings

        if not is_configured():
            logger.warning("Scheduled backup skipped: not configured.")
            return

        # Acquire a transaction-level PostgreSQL advisory lock so that only
        # one Gunicorn worker proceeds even when multiple workers all fire the
        # scheduled job at the same time.  The lock is released automatically
        # when the transaction commits (inside Settings.set below).
        locked = db.session.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not locked:
            logger.info(
                "Scheduled backup skipped: another worker holds the advisory lock."
            )
            return

        if Settings.get("backup_last_status") == "running":
            logger.warning("Scheduled backup skipped: already running.")
            return

        Settings.set("backup_last_status", "running")
        # ↑ commits the transaction, which releases the advisory lock.
        # Any worker that now acquires the lock will see status="running" and skip.
        Settings.set("backup_last_run", datetime.now().isoformat())

    _backup_thread(app)

"""Database backup service: pg_dump specific tables and upload via SFTP."""

import io
import logging
import os
import posixpath
import re
import subprocess
import tempfile
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# Stable bigint used as a PostgreSQL advisory lock key to prevent concurrent
# backup runs across multiple Gunicorn workers.  Value is ASCII "MAND".
_ADVISORY_LOCK_KEY = 0x4D414E44

# All tables to include in backup — excludes voters and voters_backup_* tables
BACKUP_TABLES = [
    "users",
    "signatures",
    "books",
    "batches",
    "collectors",
    "organizations",
    "data_enterers",
    "paid_collectors",
    "settings",
    "voter_imports",
    "petition_print_jobs",
]


def create_local_dump() -> str:
    """Run pg_dump of BACKUP_TABLES and return the path to the temp file.

    Caller is responsible for deleting the file after use.
    Raises RuntimeError on failure.
    """
    from flask import current_app
    from app import db
    from sqlalchemy import text

    db_url = os.environ.get("DATABASE_URL") or current_app.config.get(
        "SQLALCHEMY_DATABASE_URI", ""
    )
    try:
        version_num = db.session.execute(text("SHOW server_version_num")).scalar()
        server_major = int(version_num) // 10000
    except Exception:
        server_major = None

    return _create_pg_dump(db_url, server_major)


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


def is_configured() -> bool:
    """Return True if at least one backup destination is enabled and configured."""
    return is_remote_configured() or is_local_configured()


def run_backup_async(app) -> None:
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


def _backup_thread(app) -> None:
    """Background thread: dump the database and send to each configured destination."""
    with app.app_context():
        from app import db
        from app.models import Settings
        from sqlalchemy import text

        db_url = os.environ.get("DATABASE_URL") or app.config.get(
            "SQLALCHEMY_DATABASE_URI", ""
        )
        scp_config = {
            "host": Settings.get("backup_scp_host"),
            "port": int(Settings.get("backup_scp_port", "22") or "22"),
            "user": Settings.get("backup_scp_user"),
            "key_content": Settings.get("backup_scp_key_content"),
            "remote_path": Settings.get("backup_scp_remote_path"),
        }
        local_path = (Settings.get("backup_local_path") or "").strip()

        # Determine the PostgreSQL server major version so we can pick the
        # matching pg_dump binary (avoids "server version mismatch" errors).
        try:
            version_num = db.session.execute(
                text("SHOW server_version_num")
            ).scalar()
            server_major = int(version_num) // 10000
        except Exception:
            server_major = None

        schedule = Settings.get("backup_schedule", "")
        do_remote = is_remote_configured()
        do_local = is_local_configured()

        dump_file = None
        errors: list[str] = []
        try:
            dump_file = _create_pg_dump(db_url, server_major)

            if do_remote:
                try:
                    _sftp_upload(dump_file, scp_config, schedule=schedule)
                except Exception as exc:
                    logger.exception("Remote backup failed")
                    errors.append(f"remote: {str(exc)[:200]}")

            if do_local:
                try:
                    _local_save(dump_file, local_path, schedule=schedule)
                except Exception as exc:
                    logger.exception("Local backup failed")
                    errors.append(f"local: {str(exc)[:200]}")

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


def _find_pg_dump(server_major: int | None) -> str:
    """Return the path to a pg_dump binary matching *server_major*.

    On Debian/Ubuntu, versioned binaries live at
    /usr/lib/postgresql/{version}/bin/pg_dump.  Falls back to whatever
    'pg_dump' resolves to in PATH if no versioned binary is found.
    """
    import shutil

    if server_major:
        versioned = f"/usr/lib/postgresql/{server_major}/bin/pg_dump"
        if os.path.isfile(versioned) and os.access(versioned, os.X_OK):
            logger.info("Using versioned pg_dump: %s", versioned)
            return versioned
        logger.warning(
            "pg_dump for PostgreSQL %s not found at %s; "
            "falling back to default. Install with: "
            "sudo apt install postgresql-client-%s",
            server_major, versioned, server_major,
        )

    default = shutil.which("pg_dump") or "pg_dump"
    return default


def _create_pg_dump(db_url: str, server_major: int | None = None) -> str:
    """Run pg_dump for BACKUP_TABLES only and return the path to the dump file."""
    # Strip SQLAlchemy driver prefixes (e.g. postgresql+psycopg2://)
    clean_url = db_url.replace("+psycopg2", "").replace("+pg8000", "")

    pg_dump = _find_pg_dump(server_major)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    fd, dump_path = tempfile.mkstemp(suffix=f"-petition-qc-{timestamp}.dump")
    os.close(fd)

    # Pass the full URI via --dbname so pg_dump receives the password and all
    # connection parameters exactly as SQLAlchemy uses them.
    cmd = [pg_dump, "--format=custom", "--dbname", clean_url]
    for table in BACKUP_TABLES:
        cmd.extend(["--table", table])

    with open(dump_path, "wb") as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)

    if result.returncode != 0:
        os.unlink(dump_path)
        stderr = result.stderr.decode().strip()
        # Provide an actionable message when the version mismatch is the cause.
        if "server version mismatch" in stderr and server_major:
            raise RuntimeError(
                f"pg_dump version mismatch (server is PostgreSQL {server_major}). "
                f"Install the matching client: sudo apt install postgresql-client-{server_major}"
            )
        raise RuntimeError(
            f"pg_dump exited with code {result.returncode}: {stderr}"
        )

    return dump_path


def _load_pkey(key_content: str):
    """Load a paramiko PKey from a PEM/OpenSSH string, trying all key types.

    Normalises line endings first so browser-uploaded files work regardless
    of whether they were saved with LF or CRLF.
    """
    import paramiko

    key_content = key_content.replace("\r\n", "\n").replace("\r", "\n")
    for key_class in (
        paramiko.RSAKey,
        paramiko.Ed25519Key,
        paramiko.ECDSAKey,
    ):
        try:
            return key_class.from_private_key(io.StringIO(key_content))
        except (paramiko.SSHException, ValueError):
            continue
    raise ValueError(
        "Could not load the private key — unsupported format or corrupted file. "
        "Supported types: RSA, Ed25519, ECDSA."
    )


def _make_ssh_client(scp_config: dict, timeout: int):
    """Return a connected paramiko SSHClient using the stored private key.

    Lets paramiko negotiate the pubkey algorithm freely (tries rsa-sha2-512,
    rsa-sha2-256, then ssh-rsa in order) so it works with both modern and
    legacy SSH servers.
    """
    import paramiko

    pkey = _load_pkey(scp_config["key_content"])
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.WarningPolicy())
    client.connect(
        scp_config["host"],
        port=scp_config["port"],
        username=scp_config["user"],
        pkey=pkey,
        look_for_keys=False,
        allow_agent=False,
        timeout=timeout,
    )
    return client


def test_sftp_connection(scp_config: dict, password: str | None = None) -> tuple[bool, str]:
    """Attempt an SSH connection and return (success, message).

    If *password* is supplied, only password auth is tried.

    Otherwise, key auth is attempted three ways to maximise diagnostic value:
      A) Auto  — let paramiko negotiate (rsa-sha2-512 → rsa-sha2-256 → ssh-rsa)
      B) SHA-2 — disable legacy ssh-rsa entirely
      C) SHA-1 — disable rsa-sha2 variants (legacy servers only)

    The first strategy that succeeds is reported.  If all three fail the
    combined error messages are returned so the caller can see exactly where
    negotiation is breaking down.
    """
    import paramiko

    host = scp_config["host"]

    if password:
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.WarningPolicy())
            client.connect(
                host,
                port=scp_config["port"],
                username=scp_config["user"],
                password=password,
                look_for_keys=False,
                allow_agent=False,
                timeout=8,
            )
            client.close()
            return True, f"Connected to {host} via password successfully."
        except Exception as exc:
            return False, f"Password auth failed: {exc}"

    try:
        pkey = _load_pkey(scp_config["key_content"])
    except ValueError as exc:
        return False, str(exc)

    # Capture paramiko's transport-level DEBUG log for the first attempt so
    # the caller can see exactly what the server is accepting/rejecting.
    log_stream = io.StringIO()
    log_handler = logging.StreamHandler(log_stream)
    log_handler.setLevel(logging.DEBUG)
    paramiko_log = logging.getLogger("paramiko")
    original_level = paramiko_log.level
    paramiko_log.setLevel(logging.DEBUG)
    paramiko_log.addHandler(log_handler)

    strategies = [
        ("Auto (rsa-sha2-512/256/ssh-rsa)",    {}),
        ("SHA-2 only (rsa-sha2-512/256)",       {"disabled_algorithms": {"pubkeys": ["ssh-rsa"]}}),
        ("Legacy SHA-1 only (ssh-rsa)",         {"disabled_algorithms": {"pubkeys": ["rsa-sha2-512", "rsa-sha2-256"]}}),
    ]
    errors = []
    try:
        for label, extra_kwargs in strategies:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.WarningPolicy())
            try:
                client.connect(
                    host,
                    port=scp_config["port"],
                    username=scp_config["user"],
                    pkey=pkey,
                    look_for_keys=False,
                    allow_agent=False,
                    timeout=8,
                    **extra_kwargs,
                )
                client.close()
                return True, f"Connected to {host} successfully [{label}]."
            except Exception as exc:
                errors.append(f"{label}: {exc}")
            finally:
                client.close()
    finally:
        paramiko_log.removeHandler(log_handler)
        paramiko_log.setLevel(original_level)

    # Extract auth-relevant log lines to include in the error message.
    auth_lines = [
        line for line in log_stream.getvalue().splitlines()
        if any(kw in line.lower() for kw in ("userauth", "auth", "pubkey", "allowed", "banner", "service"))
    ]
    debug_snippet = " // ".join(auth_lines[-12:]) if auth_lines else ""

    summary = " | ".join(errors)
    if debug_snippet:
        summary += f"\n\nSSH debug: {debug_snippet}"
    return False, summary


def run_backup_sync(app) -> None:
    """Run a full backup synchronously (for use by the scheduler)."""
    with app.app_context():
        from app import db
        from app.models import Settings
        from sqlalchemy import text

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


# ---------------------------------------------------------------------------
# Retention helpers
# ---------------------------------------------------------------------------

_BACKUP_RE = re.compile(r"^petition-qc-backup-(\d{8})-(\d{6})\.dump$")


def _parse_backup_dt(filename: str) -> datetime | None:
    """Parse the timestamp embedded in a backup filename, or return None."""
    m = _BACKUP_RE.match(filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _apply_retention(sftp, remote_dir: str, schedule: str) -> None:
    """Delete remote backup files that fall outside the retention policy.

    Retention rules
    ───────────────
    hourly : last 24 hourlies  +  last 7 daily (02:00) slots
                               +  last 4 weekly (Sun 02:00) slots
    daily  : last 7 dailies    +  last 4 weekly (Sun 02:00) slots
    weekly : last 4 weeklies
    """
    if schedule not in ("hourly", "daily", "weekly"):
        return

    try:
        names = sftp.listdir(remote_dir)
    except Exception as exc:
        logger.warning("Retention: could not list %s: %s", remote_dir, exc)
        return

    # Build sorted list (newest first) of (datetime, filename) pairs.
    backups = sorted(
        ((dt, n) for n in names if (dt := _parse_backup_dt(n)) is not None),
        reverse=True,
    )

    keep: set[str] = set()

    if schedule == "hourly":
        # Last 24 hourlies (any time)
        for _, name in backups[:24]:
            keep.add(name)
        # Last 7 daily slots (02:00 UTC, any weekday)
        dailies = [(dt, n) for dt, n in backups if dt.hour == 2 and dt.minute == 0]
        for _, name in dailies[:7]:
            keep.add(name)
        # Last 4 weekly slots (Sunday 02:00 UTC)
        weeklies = [(dt, n) for dt, n in dailies if dt.weekday() == 6]
        for _, name in weeklies[:4]:
            keep.add(name)

    elif schedule == "daily":
        # Last 7 dailies (any time, since only one runs per day)
        for _, name in backups[:7]:
            keep.add(name)
        # Last 4 weekly slots (Sunday 02:00 UTC)
        weeklies = [(dt, n) for dt, n in backups if dt.weekday() == 6 and dt.hour == 2 and dt.minute == 0]
        for _, name in weeklies[:4]:
            keep.add(name)

    elif schedule == "weekly":
        # Last 4 weeklies
        for _, name in backups[:4]:
            keep.add(name)

    for dt, name in backups:
        if name not in keep:
            path = posixpath.join(remote_dir, name)
            try:
                sftp.remove(path)
                logger.info("Retention: removed %s", name)
            except Exception as exc:
                logger.warning("Retention: could not remove %s: %s", name, exc)


def _apply_local_retention(local_dir: str, schedule: str) -> None:
    """Delete local backup files that fall outside the retention policy.

    Uses the same keep rules as _apply_retention (the SFTP version).
    """
    if schedule not in ("hourly", "daily", "weekly"):
        return

    try:
        names = os.listdir(local_dir)
    except Exception as exc:
        logger.warning("Local retention: could not list %s: %s", local_dir, exc)
        return

    backups = sorted(
        ((dt, n) for n in names if (dt := _parse_backup_dt(n)) is not None),
        reverse=True,
    )

    keep: set[str] = set()

    if schedule == "hourly":
        for _, name in backups[:24]:
            keep.add(name)
        dailies = [(dt, n) for dt, n in backups if dt.hour == 2 and dt.minute == 0]
        for _, name in dailies[:7]:
            keep.add(name)
        weeklies = [(dt, n) for dt, n in dailies if dt.weekday() == 6]
        for _, name in weeklies[:4]:
            keep.add(name)
    elif schedule == "daily":
        for _, name in backups[:7]:
            keep.add(name)
        weeklies = [
            (dt, n) for dt, n in backups
            if dt.weekday() == 6 and dt.hour == 2 and dt.minute == 0
        ]
        for _, name in weeklies[:4]:
            keep.add(name)
    elif schedule == "weekly":
        for _, name in backups[:4]:
            keep.add(name)

    for dt, name in backups:
        if name not in keep:
            path = os.path.join(local_dir, name)
            try:
                os.remove(path)
                logger.info("Local retention: removed %s", name)
            except Exception as exc:
                logger.warning("Local retention: could not remove %s: %s", name, exc)


def _local_save(dump_path: str, local_dir: str, schedule: str = "") -> None:
    """Copy the dump file to local_dir and apply the local retention policy."""
    import shutil

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_filename = f"petition-qc-backup-{timestamp}.dump"
    dest_path = os.path.join(local_dir, dest_filename)
    try:
        shutil.copy2(dump_path, dest_path)
    except Exception as exc:
        raise RuntimeError(f"Local backup copy failed: {exc}") from exc
    _apply_local_retention(local_dir, schedule)


# ---------------------------------------------------------------------------
# SFTP upload
# ---------------------------------------------------------------------------

def _sftp_upload(local_path: str, scp_config: dict, schedule: str = "") -> None:
    """Upload *local_path* to the remote server and apply the retention policy."""
    client = _make_ssh_client(scp_config, timeout=30)
    try:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        remote_filename = f"petition-qc-backup-{timestamp}.dump"
        remote_dir = scp_config["remote_path"].rstrip("/")
        remote_path = posixpath.join(remote_dir, remote_filename)

        sftp = client.open_sftp()
        sftp.put(local_path, remote_path)
        _apply_retention(sftp, remote_dir, schedule)
        sftp.close()
    finally:
        client.close()

"""SFTP backup destination: SSH key handling, connection test, upload, retention."""

import io
import logging
import posixpath
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import paramiko

from app.services.backup_retention import (
    LEGACY_PREFIXES,
    BackupNaming,
    select_backups_to_keep,
    sort_backup_names,
)

logger = logging.getLogger(__name__)


def load_pkey(key_content: str) -> paramiko.PKey:
    """Load a paramiko PKey from a PEM/OpenSSH string, trying all key types.

    Normalises line endings first so browser-uploaded files work regardless
    of whether they were saved with LF or CRLF.
    """
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


def make_ssh_client(scp_config: dict, timeout: int) -> paramiko.SSHClient:
    """Return a connected paramiko SSHClient using the stored private key.

    Lets paramiko negotiate the pubkey algorithm freely (tries rsa-sha2-512,
    rsa-sha2-256, then ssh-rsa in order) so it works with both modern and
    legacy SSH servers.
    """
    pkey = load_pkey(scp_config["key_content"])
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


_KEY_STRATEGIES: list[tuple[str, dict[str, Any]]] = [
    ("Auto (rsa-sha2-512/256/ssh-rsa)",    {}),
    ("SHA-2 only (rsa-sha2-512/256)",       {"disabled_algorithms": {"pubkeys": ["ssh-rsa"]}}),
    ("Legacy SHA-1 only (ssh-rsa)",         {"disabled_algorithms": {"pubkeys": ["rsa-sha2-512", "rsa-sha2-256"]}}),
]

_AUTH_LOG_KEYWORDS = ("userauth", "auth", "pubkey", "allowed", "banner", "service")


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
    if password:
        return _test_password_auth(scp_config, password)

    try:
        pkey = load_pkey(scp_config["key_content"])
    except ValueError as exc:
        return False, str(exc)

    # Capture paramiko's transport-level DEBUG log so the caller can see
    # exactly what the server is accepting/rejecting.
    log_stream = io.StringIO()
    log_handler = logging.StreamHandler(log_stream)
    log_handler.setLevel(logging.DEBUG)
    paramiko_log = logging.getLogger("paramiko")
    original_level = paramiko_log.level
    paramiko_log.setLevel(logging.DEBUG)
    paramiko_log.addHandler(log_handler)
    try:
        ok, message = _try_key_strategies(scp_config, pkey)
    finally:
        paramiko_log.removeHandler(log_handler)
        paramiko_log.setLevel(original_level)

    if ok:
        return True, message
    debug_snippet = _auth_debug_snippet(log_stream.getvalue())
    if debug_snippet:
        message += f"\n\nSSH debug: {debug_snippet}"
    return False, message


def _test_password_auth(scp_config: dict, password: str) -> tuple[bool, str]:
    """Try password-only SSH auth and return (success, message)."""
    host = scp_config["host"]
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


def _try_key_strategies(scp_config: dict, pkey: paramiko.PKey) -> tuple[bool, str]:
    """Try each key-auth strategy; return the first success or joined errors."""
    host = scp_config["host"]
    errors = []
    for label, extra_kwargs in _KEY_STRATEGIES:
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
            return True, f"Connected to {host} successfully [{label}]."
        except Exception as exc:
            errors.append(f"{label}: {exc}")
        finally:
            client.close()
    return False, " | ".join(errors)


def _auth_debug_snippet(log_text: str) -> str:
    """Return the last auth-related paramiko log lines joined for display."""
    auth_lines = [
        line for line in log_text.splitlines()
        if any(kw in line.lower() for kw in _AUTH_LOG_KEYWORDS)
    ]
    return " // ".join(auth_lines[-12:]) if auth_lines else ""


def apply_sftp_retention(
    sftp: Any, remote_dir: str, schedule: str, prefixes: Iterable[str] = LEGACY_PREFIXES
) -> None:
    """Delete remote backup files that fall outside the retention policy."""
    if schedule not in ("hourly", "daily", "weekly"):
        return

    try:
        names = sftp.listdir(remote_dir)
    except Exception as exc:
        logger.warning("Retention: could not list %s: %s", remote_dir, exc)
        return

    backups = sort_backup_names(names, prefixes)
    keep = select_backups_to_keep(backups, schedule)

    for _, name in backups:
        if name not in keep:
            path = posixpath.join(remote_dir, name)
            try:
                sftp.remove(path)
                logger.info("Retention: removed %s", name)
            except Exception as exc:
                logger.warning("Retention: could not remove %s: %s", name, exc)


def sftp_upload(
    local_path: str,
    scp_config: dict,
    schedule: str = "",
    filename: str | None = None,
    prefixes: Iterable[str] = LEGACY_PREFIXES,
) -> None:
    """Upload *local_path* to the remote server and apply the retention policy."""
    client = make_ssh_client(scp_config, timeout=30)
    try:
        remote_filename = filename or BackupNaming().filename(datetime.now())
        remote_dir = scp_config["remote_path"].rstrip("/")
        remote_path = posixpath.join(remote_dir, remote_filename)

        sftp = client.open_sftp()
        sftp.put(local_path, remote_path)
        apply_sftp_retention(sftp, remote_dir, schedule, prefixes)
        sftp.close()
    finally:
        client.close()

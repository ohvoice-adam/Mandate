"""
Grouped settings helpers mixed into the ``Settings`` model.

Each mixin is a set of classmethods built on the key/value primitives
``get``/``set`` that ``Settings`` implements; split out of
``app/models/settings.py`` to keep that file small.  Callers keep using
``Settings.get_backup_config()`` etc. exactly as before.
"""

import base64
import hashlib
import logging
import os

logger = logging.getLogger(__name__)


class _KeyValueStore:
    """Declares the primitives the mixins rely on; ``Settings`` provides them."""

    @classmethod
    def get(cls, key: str, default: str | None = None) -> str | None:
        raise NotImplementedError

    @classmethod
    def set(cls, key: str, value: str) -> None:
        raise NotImplementedError


class BackupSettingsMixin(_KeyValueStore):
    """Backup destinations, schedule, notifications and digest queue."""

    @classmethod
    def get_backup_config(cls) -> dict:
        """Return all backup-related settings as a dict."""
        return {
            "scp_host": cls.get("backup_scp_host", ""),
            "scp_port": cls.get("backup_scp_port", "22"),
            "scp_user": cls.get("backup_scp_user", ""),
            "has_key": bool(cls.get("backup_scp_key_content")),
            "key_fingerprint": cls._compute_key_fingerprint(),
            "scp_remote_path": cls.get("backup_scp_remote_path", ""),
            "schedule": cls.get("backup_schedule", ""),
            "last_run": cls.get("backup_last_run", ""),
            "last_status": cls.get("backup_last_status", ""),
            "enable_remote": cls.get("backup_enable_remote", "true"),
            "enable_local": cls.get("backup_enable_local", "false"),
            "local_path": cls.get("backup_local_path", ""),
            "enable_gdrive": cls.get("backup_enable_gdrive", "false"),
            "gdrive_folder_id": cls.get("backup_gdrive_folder_id", ""),
            "has_gdrive_key": bool(cls.get("backup_gdrive_sa_json")),
            "gdrive_client_email": cls._gdrive_client_email(),
        }

    @classmethod
    def _gdrive_client_email(cls) -> str:
        """Return the stored Google service account's email, or ''."""
        from app.services.backup_gdrive import client_email
        return client_email(cls.get("backup_gdrive_sa_json"))

    @classmethod
    def _compute_key_fingerprint(cls) -> str:
        """Return the SHA-256 fingerprint of the stored key (OpenSSH format)."""
        key_content = cls.get("backup_scp_key_content")
        if not key_content:
            return ""
        try:
            from app.services.backup_sftp import load_pkey
            pkey = load_pkey(key_content)
            digest = hashlib.sha256(pkey.asbytes()).digest()
            return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")
        except Exception:
            logger.warning("Could not compute SSH key fingerprint", exc_info=True)
            return "(error computing fingerprint)"

    @classmethod
    def get_backup_notify_config(cls) -> dict:
        """Return backup notification settings as a dict."""
        return {
            "notify_email": cls.get("backup_notify_email", ""),
            "notify_success": cls.get("backup_notify_success", ""),
            "notify_failure": cls.get("backup_notify_failure", "false"),
        }

    @classmethod
    def save_backup_notify_config(cls, notify_email: str, notify_success: str, notify_failure: str) -> None:
        """Persist backup notification configuration."""
        cls.set("backup_notify_email", notify_email)
        cls.set("backup_notify_success", notify_success)
        cls.set("backup_notify_failure", notify_failure)

    @classmethod
    def get_email_context(cls) -> dict:
        """Return shared context dict for backup notification emails.

        site_url resolution: DB key ``site_url`` → ``CAMPAIGN1_DOMAIN`` env var
        → ``""`` (caller renders no link).
        """
        site_url = cls.get("site_url", "").strip()
        if not site_url:
            domain = os.environ.get("CAMPAIGN1_DOMAIN", "").strip()
            if domain:
                site_url = f"https://{domain}"
        return {
            "site_url": site_url,
            "org_name": cls.get("branding_org_name", "").strip(),
        }

    @classmethod
    def get_digest_pending(cls) -> list:
        """Return list of ISO timestamps queued for digest email."""
        raw = cls.get("backup_digest_pending", "")
        return [ts for ts in raw.split("\n") if ts.strip()] if raw else []

    @classmethod
    def add_digest_pending(cls, iso_ts: str) -> None:
        """Append a timestamp to the digest pending list."""
        existing = cls.get("backup_digest_pending", "") or ""
        entries = [ts for ts in existing.split("\n") if ts.strip()]
        entries.append(iso_ts)
        cls.set("backup_digest_pending", "\n".join(entries))

    @classmethod
    def clear_digest_pending(cls) -> None:
        """Clear the digest pending list."""
        cls.set("backup_digest_pending", "")

    @classmethod
    def save_backup_config(
        cls,
        host: str,
        port: str,
        user: str,
        remote_path: str,
        key_content: str | None = None,
        enable_remote: str = "true",
        enable_local: str = "false",
        local_path: str = "",
        enable_gdrive: str = "false",
        gdrive_folder_id: str = "",
        gdrive_sa_json: str | None = None,
    ) -> None:
        """Persist SCP backup configuration and destination settings.

        If *key_content* is provided it replaces any previously stored key.
        Omit (or pass None) to keep the existing stored key unchanged.

        If *enable_remote* / *enable_local* are omitted the defaults leave remote
        enabled and local disabled, matching fresh-install behaviour.
        *local_path* is stripped of leading/trailing whitespace before storage.

        *gdrive_sa_json* follows the same rule as *key_content*: None keeps the
        stored Google service-account key.
        """
        cls.set("backup_scp_host", host.strip())
        cls.set("backup_scp_port", port.strip() or "22")
        cls.set("backup_scp_user", user.strip())
        cls.set("backup_scp_remote_path", remote_path.strip())
        if key_content is not None:
            cls.set("backup_scp_key_content", key_content)
        cls.set("backup_enable_remote", "true" if enable_remote in ("true", "1", "on") else "false")
        cls.set("backup_enable_local",  "true" if enable_local  in ("true", "1", "on") else "false")
        cls.set("backup_local_path", local_path.strip())
        cls.set("backup_enable_gdrive", "true" if enable_gdrive in ("true", "1", "on") else "false")
        cls.set("backup_gdrive_folder_id", gdrive_folder_id.strip())
        if gdrive_sa_json is not None:
            cls.set("backup_gdrive_sa_json", gdrive_sa_json)


class SmtpSettingsMixin(_KeyValueStore):
    """Outgoing email (SMTP) configuration."""

    @classmethod
    def get_smtp_config(cls) -> dict:
        """Return SMTP settings, falling back to env vars when DB is unset.

        Environment variables (SMTP_HOST, SMTP_PORT, SMTP_USER,
        SMTP_FROM_EMAIL, SMTP_PASSWORD, SMTP_USE_TLS) act as defaults so all
        campaigns can share a single SMTP configuration set in .env without
        requiring admin UI setup on each instance.  A value stored in the DB
        via the admin UI always takes precedence over the environment.
        """
        def _smtp(key: str, env_var: str, default: str = "") -> str:
            return cls.get(key) or os.environ.get(env_var, default)

        return {
            "host":       _smtp("smtp_host",       "SMTP_HOST"),
            "port":       _smtp("smtp_port",       "SMTP_PORT", "587"),
            "user":       _smtp("smtp_user",       "SMTP_USER"),
            "from_email": _smtp("smtp_from_email", "SMTP_FROM_EMAIL"),
            "use_tls":    _smtp("smtp_use_tls",    "SMTP_USE_TLS", "true"),
            "has_password": bool(
                cls.get("smtp_password") or os.environ.get("SMTP_PASSWORD")
            ),
        }

    @classmethod
    def save_smtp_config(
        cls,
        host: str,
        port: str,
        user: str,
        from_email: str,
        use_tls: bool,
        password: str | None = None,
    ) -> None:
        """Persist SMTP configuration. Password is only overwritten if provided."""
        cls.set("smtp_host", host.strip())
        cls.set("smtp_port", port.strip() or "587")
        cls.set("smtp_user", user.strip())
        cls.set("smtp_from_email", from_email.strip())
        cls.set("smtp_use_tls", "true" if use_tls else "false")
        if password:
            cls.set("smtp_password", password)


class BrandingSettingsMixin(_KeyValueStore):
    """Logo, colors and fonts."""

    @classmethod
    def get_branding_config(cls) -> dict:
        """Return all branding-related settings as a dict."""
        return {
            "mode": cls.get("branding_mode", ""),
            "org_name": cls.get("branding_org_name", ""),
            "has_logo": bool(cls.get("branding_logo_content")),
            "logo_mime": cls.get("branding_logo_mime", "image/png"),
            "primary_color": cls.get("branding_primary_color", ""),
            "accent_color": cls.get("branding_accent_color", ""),
        }

    @classmethod
    def save_branding_config(cls, mode: str, org_name: str, primary_color: str, accent_color: str) -> None:
        """Persist branding configuration."""
        cls.set("branding_mode", mode)
        cls.set("branding_org_name", org_name)
        cls.set("branding_primary_color", primary_color)
        cls.set("branding_accent_color", accent_color)

    @classmethod
    def get_branding_fonts(cls) -> dict:
        """Return the configured headline and body font names."""
        from app.services.fonts import DEFAULT_BODY_FONT, DEFAULT_HEADLINE_FONT
        return {
            "headline_font": cls.get("branding_headline_font", DEFAULT_HEADLINE_FONT),
            "body_font": cls.get("branding_body_font", DEFAULT_BODY_FONT),
        }

    @classmethod
    def save_branding_fonts(cls, headline_font: str, body_font: str) -> None:
        """Persist font choices."""
        cls.set("branding_headline_font", headline_font)
        cls.set("branding_body_font", body_font)

    @classmethod
    def get_logo_bytes(cls) -> bytes | None:
        """Decode and return raw logo bytes, or None."""
        content = cls.get("branding_logo_content")
        if not content:
            return None
        try:
            return base64.b64decode(content)
        except ValueError:
            logger.warning("Stored branding logo is not valid base64")
            return None

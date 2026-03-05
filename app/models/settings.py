from app import db


class Settings(db.Model):
    """Application settings stored in the database."""

    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    @classmethod
    def get(cls, key: str, default: str = None) -> str:
        """Get a setting value by key."""
        setting = cls.query.filter_by(key=key).populate_existing().first()
        return setting.value if setting else default

    @classmethod
    def set(cls, key: str, value: str) -> None:
        """Set a setting value."""
        setting = cls.query.filter_by(key=key).first()
        if setting:
            setting.value = value
        else:
            setting = cls(key=key, value=value)
            db.session.add(setting)
        db.session.commit()

    @classmethod
    def get_target_city(cls) -> str:
        """Get the target city for signature verification."""
        return cls.get("target_city", "COLUMBUS CITY")

    @classmethod
    def get_target_city_display(cls) -> str:
        """Get the target city in title case for display."""
        city = cls.get_target_city()
        return city.title() if city else "Columbus"

    @classmethod
    def get_target_city_pattern(cls) -> str:
        """Get the SQL LIKE pattern for matching the target city."""
        city = cls.get_target_city()
        if city:
            # Remove " CITY" suffix for pattern matching if present
            base = city.replace(" CITY", "").replace(" city", "")
            return f"{base}%"
        return "COLUMBUS%"

    @classmethod
    def get_signature_goal(cls) -> int:
        """Get the signature goal count."""
        value = cls.get("signature_goal")
        try:
            return int(value) if value else 0
        except (ValueError, TypeError):
            return 0

    @classmethod
    def set_signature_goal(cls, goal: int) -> None:
        """Set the signature goal count."""
        cls.set("signature_goal", str(goal))

    # ------------------------------------------------------------------
    # Backup settings
    # ------------------------------------------------------------------

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
        }

    @classmethod
    def _compute_key_fingerprint(cls) -> str:
        """Return the SHA-256 fingerprint of the stored key (OpenSSH format)."""
        key_content = cls.get("backup_scp_key_content")
        if not key_content:
            return ""
        try:
            import base64
            import hashlib
            from app.services.backup import _load_pkey
            pkey = _load_pkey(key_content)
            digest = hashlib.sha256(pkey.asbytes()).digest()
            return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")
        except Exception:
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
    ) -> None:
        """Persist SCP backup configuration.

        If *key_content* is provided it replaces any previously stored key.
        Omit (or pass None) to keep the existing stored key unchanged.
        """
        cls.set("backup_scp_host", host.strip())
        cls.set("backup_scp_port", port.strip() or "22")
        cls.set("backup_scp_user", user.strip())
        cls.set("backup_scp_remote_path", remote_path.strip())
        if key_content is not None:
            cls.set("backup_scp_key_content", key_content)

    # ------------------------------------------------------------------
    # SMTP / email settings
    # ------------------------------------------------------------------

    @classmethod
    def get_smtp_config(cls) -> dict:
        """Return all SMTP-related settings as a dict."""
        return {
            "host": cls.get("smtp_host", ""),
            "port": cls.get("smtp_port", "587"),
            "user": cls.get("smtp_user", ""),
            "from_email": cls.get("smtp_from_email", ""),
            "use_tls": cls.get("smtp_use_tls", "true"),
            "has_password": bool(cls.get("smtp_password")),
        }

    @classmethod
    def save_smtp_config(cls, host, port, user, from_email, use_tls, password=None):
        """Persist SMTP configuration. Password is only overwritten if provided."""
        cls.set("smtp_host", host.strip())
        cls.set("smtp_port", port.strip() or "587")
        cls.set("smtp_user", user.strip())
        cls.set("smtp_from_email", from_email.strip())
        cls.set("smtp_use_tls", "true" if use_tls else "false")
        if password:
            cls.set("smtp_password", password)

    # ------------------------------------------------------------------
    # Branding settings
    # ------------------------------------------------------------------

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
    def get_logo_bytes(cls) -> bytes | None:
        """Decode and return raw logo bytes, or None."""
        content = cls.get("branding_logo_content")
        if not content:
            return None
        import base64
        try:
            return base64.b64decode(content)
        except Exception:
            return None

    def __repr__(self):
        return f"<Settings {self.key}={self.value}>"

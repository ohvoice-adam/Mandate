"""
Application settings stored in the database as key-value pairs.

SQLAlchemy / pattern concepts used here:
- **@classmethod**: all public methods are classmethods because callers use
  ``Settings.get("key")`` without needing an instance.  This is the
  *repository pattern* — the class itself acts as the data-access layer.
- **db.session.add(obj)**: stages a new row for INSERT.  Nothing hits the DB
  until ``db.session.commit()``.
- **db.session.commit()**: writes all staged changes in a single transaction.
  ``Settings.set()`` calls it immediately so every write is durable.
- **populate_existing()**: forces SQLAlchemy to refresh the object from the DB
  even if it already exists in the session's identity map.  Needed for
  ``get()`` so stale cached values are not returned across requests.
"""

from app import db

# Suffixes appended by some county voter-file exports to denote an incorporated city.
# "Columbus City" (space) and "Grove City-City" (hyphen) both mean the same
# jurisdiction as the bare name "Columbus" / "Grove City".
_CITY_SUFFIXES: list[str] = [" CITY", "-CITY"]


class Settings(db.Model):
    """
    Key-value store for all runtime configuration.

    All app configuration (SMTP credentials, branding colours, backup
    schedules, signature goals, etc.) lives in this table rather than
    environment variables so that admins can change settings through the UI
    without restarting the server.
    """

    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    # index=True speeds up the .filter_by(key=...) lookup inside get().
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text)
    # onupdate=db.func.now() tells SQLAlchemy to set this column to the
    # current timestamp automatically on every UPDATE statement.
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    @classmethod
    def get(cls, key: str, default: str = None) -> str:
        """
        Read a setting value by key.

        Args:
            key:     The setting key (e.g. ``"smtp_host"``).
            default: Value to return when the key doesn't exist in the DB.

        Returns:
            The stored string value, or *default* if not found.
        """
        # populate_existing() bypasses SQLAlchemy's in-session cache so we
        # always read the latest value from the DB, not a stale object.
        setting = cls.query.filter_by(key=key).populate_existing().first()
        return setting.value if setting else default

    @classmethod
    def set(cls, key: str, value: str) -> None:
        """
        Write a setting value, inserting a new row if the key doesn't exist.

        Args:
            key:   The setting key.
            value: The string value to store.
        """
        setting = cls.query.filter_by(key=key).first()
        if setting:
            setting.value = value  # UPDATE existing row
        else:
            setting = cls(key=key, value=value)
            db.session.add(setting)  # Stage INSERT — nothing written yet
        db.session.commit()  # Flush all staged changes to the DB in one transaction

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
    def get_city_aliases(cls) -> list[str]:
        """
        Return all city-name variants in the voter data that are equivalent to
        the configured target city.

        Some county voter files append " City" or "-City" to municipality names
        (e.g. "Columbus City", "Grove City-City") while other counties write
        just "Columbus" or "Grove City" — both referring to the same place.

        Aliases are only added when the alternate form actually exists in the
        voters table, preventing incorrect merges: "Grove City" is NOT merged
        with "Grove" unless "Grove" literally appears in the voter data.
        """
        from sqlalchemy import text as sa_text

        target = cls.get_target_city() or "COLUMBUS"
        target_upper = target.upper().strip()

        rows = db.session.execute(sa_text(
            "SELECT DISTINCT upper(city) FROM voters "
            "WHERE city IS NOT NULL AND city != ''"
        )).fetchall()
        cities_in_db = {row[0] for row in rows}

        aliases = [target_upper]

        # Strip one trailing suffix and check whether the bare base form exists
        for suffix in _CITY_SUFFIXES:
            if target_upper.endswith(suffix):
                stripped = target_upper[: -len(suffix)]
                if stripped in cities_in_db and stripped not in aliases:
                    aliases.append(stripped)
                break  # at most one suffix applies to the stored target

        # Check whether target + each suffix exists as a variant
        for suffix in _CITY_SUFFIXES:
            extended = target_upper + suffix
            if extended in cities_in_db and extended not in aliases:
                aliases.append(extended)

        return aliases

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
        """Return SMTP settings, falling back to env vars when DB is unset.

        Environment variables (SMTP_HOST, SMTP_PORT, SMTP_USER,
        SMTP_FROM_EMAIL, SMTP_PASSWORD, SMTP_USE_TLS) act as defaults so all
        campaigns can share a single SMTP configuration set in .env without
        requiring admin UI setup on each instance.  A value stored in the DB
        via the admin UI always takes precedence over the environment.
        """
        import os

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
    def get_branding_fonts(cls) -> dict:
        """Return the configured headline and body font names."""
        from app.services.fonts import DEFAULT_HEADLINE_FONT, DEFAULT_BODY_FONT
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
        import base64
        try:
            return base64.b64decode(content)
        except Exception:
            return None

    def __repr__(self):
        return f"<Settings {self.key}={self.value}>"

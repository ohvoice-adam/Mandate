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
from app.models.settings_groups import (
    BackupSettingsMixin,
    BrandingSettingsMixin,
    SmtpSettingsMixin,
)

# Suffixes appended by some county voter-file exports to denote an incorporated city.
# "Columbus City" (space) and "Grove City-City" (hyphen) both mean the same
# jurisdiction as the bare name "Columbus" / "Grove City".
_CITY_SUFFIXES: list[str] = [" CITY", "-CITY"]


class Settings(db.Model, BackupSettingsMixin, SmtpSettingsMixin, BrandingSettingsMixin):
    """
    Key-value store for all runtime configuration.

    All app configuration (SMTP credentials, branding colours, backup
    schedules, signature goals, etc.) lives in this table rather than
    environment variables so that admins can change settings through the UI
    without restarting the server.

    Grouped helpers (backup, SMTP, branding) come from the mixins in
    ``app/models/settings_groups.py``.
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
    def get(cls, key: str, default: str | None = None) -> str | None:
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
    def get_target_city_slug(cls) -> str:
        """
        Return a snake_case identifier for the target city, suitable for use in
        column headers / machine-readable field names (e.g. "columbus",
        "grove_city"). One trailing city suffix (" CITY" / "-CITY") is dropped
        so "COLUMBUS CITY" -> "columbus" and "GROVE CITY-CITY" -> "grove_city".
        Non-alphanumeric runs collapse to single underscores.
        """
        import re

        city = (cls.get_target_city() or "COLUMBUS").upper().strip()
        for suffix in _CITY_SUFFIXES:
            if city.endswith(suffix):
                city = city[: -len(suffix)]
                break  # at most one suffix applies to the stored target
        slug = re.sub(r"[^a-z0-9]+", "_", city.lower()).strip("_")
        return slug or "target_city"

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

    def __repr__(self) -> str:
        return f"<Settings {self.key}={self.value}>"

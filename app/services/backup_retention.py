"""Backup filenames, retention policy, and the local-directory destination.

Backups are named ``<prefix>-backup-YYYYMMDD-HHMMSS.dump``.  The prefix is the
configured organization name turned into a slug (``Ohio Voice`` →
``ohio-voice``), or ``petition-qc`` when no organization name is set.

Retention identifies backups by filename.  Every prefix used for a backup
is recorded in the ``backup_name_prefixes`` setting, and retention treats the
current prefix, every recorded prefix and legacy ``petition-qc`` as one pool.
So backups made before an organization name was set, or under a previous
organization name, still age out.  Files with any other prefix (e.g. another
app sharing the folder) are never touched.
"""

import logging
import os
import re
import shutil
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

LEGACY_PREFIX = "petition-qc"
_MAX_PREFIX_LEN = 40
PREFIX_HISTORY_KEY = "backup_name_prefixes"
_VALID_PREFIX_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

BACKUP_RE = re.compile(r"^(?P<prefix>[a-z0-9-]+)-backup-(\d{8})-(\d{6})\.dump$")


def slugify_prefix(org_name: str | None) -> str:
    """Turn an organization name into a filename-safe prefix.

    Only ``[a-z0-9-]`` survives, so the result can never contain path
    separators, dots or spaces.  Falls back to ``petition-qc`` when nothing
    usable is left.
    """
    ascii_name = (
        unicodedata.normalize("NFKD", org_name or "")
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    slug = slug[:_MAX_PREFIX_LEN].rstrip("-")
    return slug or LEGACY_PREFIX


@dataclass(frozen=True)
class BackupNaming:
    """The filename prefix for new backups and the prefixes retention manages."""

    prefix: str = LEGACY_PREFIX
    prefixes: frozenset[str] = field(default_factory=lambda: frozenset({LEGACY_PREFIX}))

    @classmethod
    def for_org(
        cls, org_name: str | None, previous: Iterable[str] = ()
    ) -> "BackupNaming":
        """Naming for *org_name*; retention also covers legacy and *previous* prefixes."""
        prefix = slugify_prefix(org_name)
        return cls(prefix=prefix, prefixes=frozenset({prefix, LEGACY_PREFIX, *previous}))

    @classmethod
    def from_settings(cls) -> "BackupNaming":
        """Naming from the configured org name plus recorded prefixes (needs app context)."""
        from app.models import Settings

        return cls.for_org(Settings.get("branding_org_name", ""), load_prefix_history())

    def filename(self, when: datetime) -> str:
        """Backup filename for a backup taken at *when*."""
        return f"{self.prefix}-backup-{when.strftime('%Y%m%d-%H%M%S')}.dump"


def load_prefix_history() -> set[str]:
    """Return every prefix previously used for a backup (needs app context).

    Invalid entries are ignored, so a hand-edited setting can never widen
    retention to names that don't look like ours.
    """
    from app.models import Settings

    raw = Settings.get(PREFIX_HISTORY_KEY, "") or ""
    return {p for p in (line.strip() for line in raw.splitlines()) if _VALID_PREFIX_RE.match(p)}


def record_prefix_used(prefix: str) -> None:
    """Add *prefix* to the recorded history if it is new (needs app context)."""
    from app.models import Settings

    history = load_prefix_history()
    if prefix in history or not _VALID_PREFIX_RE.match(prefix):
        return
    Settings.set(PREFIX_HISTORY_KEY, "\n".join(sorted(history | {prefix})))
    logger.info("Recorded backup name prefix %r", prefix)


_LEGACY_NAMING = BackupNaming()
LEGACY_PREFIXES = _LEGACY_NAMING.prefixes


def parse_backup_dt(
    filename: str, prefixes: Iterable[str] = LEGACY_PREFIXES
) -> datetime | None:
    """Parse the timestamp of a backup whose prefix is in *prefixes*, else None."""
    m = BACKUP_RE.match(filename)
    if not m or m.group("prefix") not in prefixes:
        return None
    try:
        return datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def select_backups_to_keep(
    backups: list[tuple[datetime, str]], schedule: str
) -> set[str]:
    """Return the filenames to keep under the retention policy.

    *backups* must be sorted newest first.

    Retention rules
    ───────────────
    hourly : last 24 hourlies  +  last 7 daily (02:00) slots
                               +  last 4 weekly (Sun 02:00) slots
    daily  : last 7 dailies    +  last 4 weekly (Sun 02:00) slots
    weekly : last 4 weeklies
    """
    keep: set[str] = set()

    if schedule == "hourly":
        keep.update(name for _, name in backups[:24])
        dailies = [(dt, n) for dt, n in backups if dt.hour == 2 and dt.minute == 0]
        keep.update(name for _, name in dailies[:7])
        weeklies = [(dt, n) for dt, n in dailies if dt.weekday() == 6]
        keep.update(name for _, name in weeklies[:4])
    elif schedule == "daily":
        keep.update(name for _, name in backups[:7])
        weeklies = [
            (dt, n) for dt, n in backups
            if dt.weekday() == 6 and dt.hour == 2 and dt.minute == 0
        ]
        keep.update(name for _, name in weeklies[:4])
    elif schedule == "weekly":
        keep.update(name for _, name in backups[:4])

    return keep


def sort_backup_names(
    names: Iterable[str], prefixes: Iterable[str] = LEGACY_PREFIXES
) -> list[tuple[datetime, str]]:
    """Return (datetime, name) pairs for recognised backup files, newest first."""
    prefixes = frozenset(prefixes)
    return sorted(
        ((dt, n) for n in names if (dt := parse_backup_dt(n, prefixes)) is not None),
        reverse=True,
    )


def apply_local_retention(
    local_dir: str, schedule: str, prefixes: Iterable[str] = LEGACY_PREFIXES
) -> None:
    """Delete local backup files that fall outside the retention policy."""
    if schedule not in ("hourly", "daily", "weekly"):
        return

    try:
        names = os.listdir(local_dir)
    except Exception as exc:
        logger.warning("Local retention: could not list %s: %s", local_dir, exc)
        return

    backups = sort_backup_names(names, prefixes)
    keep = select_backups_to_keep(backups, schedule)

    for _, name in backups:
        if name not in keep:
            path = os.path.join(local_dir, name)
            try:
                os.remove(path)
                logger.info("Local retention: removed %s", name)
            except Exception as exc:
                logger.warning("Local retention: could not remove %s: %s", name, exc)


def local_save(
    dump_path: str,
    local_dir: str,
    schedule: str = "",
    filename: str | None = None,
    prefixes: Iterable[str] = LEGACY_PREFIXES,
) -> None:
    """Copy the dump file to local_dir and apply the local retention policy.

    *filename* defaults to a legacy-prefixed name stamped now.
    """
    abs_dir = os.path.realpath(local_dir)
    if not os.path.isabs(abs_dir):
        raise RuntimeError(f"Local backup directory must be an absolute path, got: {local_dir!r}")

    dest_path = os.path.join(abs_dir, filename or _LEGACY_NAMING.filename(datetime.now()))
    try:
        shutil.copy2(dump_path, dest_path)
    except Exception as exc:
        raise RuntimeError(f"Local backup copy failed: {exc}") from exc
    apply_local_retention(abs_dir, schedule, prefixes)

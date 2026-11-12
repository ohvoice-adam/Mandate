"""Backup filename parsing and retention policy shared by every destination.

Also holds the local-directory destination, which is little more than a copy
plus retention.
"""

import logging
import os
import re
import shutil
from datetime import datetime

logger = logging.getLogger(__name__)

BACKUP_RE = re.compile(r"^petition-qc-backup-(\d{8})-(\d{6})\.dump$")


def parse_backup_dt(filename: str) -> datetime | None:
    """Parse the timestamp embedded in a backup filename, or return None."""
    m = BACKUP_RE.match(filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
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


def sort_backup_names(names: list[str]) -> list[tuple[datetime, str]]:
    """Return (datetime, name) pairs for recognised backup files, newest first."""
    return sorted(
        ((dt, n) for n in names if (dt := parse_backup_dt(n)) is not None),
        reverse=True,
    )


def apply_local_retention(local_dir: str, schedule: str) -> None:
    """Delete local backup files that fall outside the retention policy."""
    if schedule not in ("hourly", "daily", "weekly"):
        return

    try:
        names = os.listdir(local_dir)
    except Exception as exc:
        logger.warning("Local retention: could not list %s: %s", local_dir, exc)
        return

    backups = sort_backup_names(names)
    keep = select_backups_to_keep(backups, schedule)

    for _, name in backups:
        if name not in keep:
            path = os.path.join(local_dir, name)
            try:
                os.remove(path)
                logger.info("Local retention: removed %s", name)
            except Exception as exc:
                logger.warning("Local retention: could not remove %s: %s", name, exc)


def local_save(dump_path: str, local_dir: str, schedule: str = "") -> None:
    """Copy the dump file to local_dir and apply the local retention policy."""
    abs_dir = os.path.realpath(local_dir)
    if not os.path.isabs(abs_dir):
        raise RuntimeError(f"Local backup directory must be an absolute path, got: {local_dir!r}")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_filename = f"petition-qc-backup-{timestamp}.dump"
    dest_path = os.path.join(abs_dir, dest_filename)
    try:
        shutil.copy2(dump_path, dest_path)
    except Exception as exc:
        raise RuntimeError(f"Local backup copy failed: {exc}") from exc
    apply_local_retention(abs_dir, schedule)

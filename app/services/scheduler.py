"""APScheduler integration for scheduled database backups."""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(timezone="UTC")

# Advisory lock key for digest sends — prevents all 4 Gunicorn workers from
# sending the same digest email simultaneously. Value is ASCII "MAND" + 1.
_DIGEST_LOCK_KEY = 0x4D414E45
_JOB_ID = "scheduled_backup"
_DIGEST_DAILY_JOB_ID = "backup_digest_daily"
_DIGEST_WEEKLY_JOB_ID = "backup_digest_weekly"


def init_app(app) -> None:
    """Start the scheduler and apply the current backup schedule."""
    if not _scheduler.running:
        _scheduler.start()
    apply_schedule(app)
    _scheduler.add_job(
        _run_digest,
        trigger=CronTrigger(hour=8, minute=0),
        id=_DIGEST_DAILY_JOB_ID,
        args=[app, "daily"],
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _run_digest,
        trigger=CronTrigger(day_of_week="sun", hour=8, minute=0),
        id=_DIGEST_WEEKLY_JOB_ID,
        args=[app, "weekly"],
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def apply_schedule(app) -> None:
    """Update the scheduled backup job to match the current settings."""
    with app.app_context():
        from app.models import Settings
        schedule = Settings.get("backup_schedule", "")

    if _scheduler.get_job(_JOB_ID):
        _scheduler.remove_job(_JOB_ID)

    trigger = _make_trigger(schedule)
    if trigger:
        _scheduler.add_job(
            _run_scheduled_backup,
            trigger=trigger,
            id=_JOB_ID,
            args=[app],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("Backup scheduled: %s", schedule)
    else:
        logger.info("Backup schedule disabled.")


def _make_trigger(schedule: str):
    if schedule == "hourly":
        return CronTrigger(minute=0)
    elif schedule == "daily":
        return CronTrigger(hour=2, minute=0)
    elif schedule == "weekly":
        return CronTrigger(day_of_week="sun", hour=2, minute=0)
    return None


def _run_scheduled_backup(app) -> None:
    from app.services.backup import run_backup_sync
    try:
        run_backup_sync(app)
    except Exception:
        logger.exception("Scheduled backup failed")


def _run_digest(app, frequency: str) -> None:
    """Send a digest email if the notify_success setting matches *frequency*."""
    with app.app_context():
        from app import db
        from app.models import Settings
        from app.services import email as email_service
        from sqlalchemy import text

        if Settings.get("backup_notify_success", "") != frequency:
            return

        notify_email = Settings.get("backup_notify_email", "").strip()
        if not notify_email or not email_service.is_configured():
            return

        # Acquire a transaction-level advisory lock so only one worker sends
        # the digest even when all workers fire the job simultaneously.
        locked = db.session.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _DIGEST_LOCK_KEY},
        ).scalar()
        if not locked:
            logger.info("Digest send skipped: another worker holds the advisory lock.")
            return

        entries = Settings.get_digest_pending()
        if not entries:
            return

        try:
            email_service.send_backup_digest_email(notify_email, entries)
            Settings.clear_digest_pending()
            # ↑ commits the transaction, releasing the advisory lock.
        except Exception:
            logger.exception("Digest email failed (%s)", frequency)

# Backup Destination Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add independent SFTP and local-filesystem backup destinations, each toggleable via checkbox, sharing a single schedule and the same retention policy.

**Architecture:** Two new boolean settings (`backup_enable_remote`, `backup_enable_local`) and one path setting (`backup_local_path`) control which destinations are active. The backup thread creates the pg_dump once, then attempts each enabled+configured destination independently — errors from one never block the other. A new `_local_save` / `_apply_local_retention` pair mirrors the existing SFTP upload/retention pattern.

**Tech Stack:** Python / Flask / SQLAlchemy / paramiko / APScheduler / Jinja2 / Tailwind CSS / vanilla JS

---

## File Map

| Action | File | What changes |
|--------|------|-------------|
| Modify | `app/models/settings.py` | `get_backup_config()` + `save_backup_config()` gain 3 new fields |
| Modify | `app/services/backup.py` | Replace `is_configured()` with 3 helpers; add `_local_save`, `_apply_local_retention`; rewrite `_backup_thread` |
| Modify | `app/routes/settings.py` | Parse new form fields; fix `test_backup_connection` to use `is_remote_configured()` |
| Modify | `app/templates/settings/index.html` | Two destination checkboxes, local path field, JS show/hide, form validation |
| Create | `tests/test_backup.py` | Tests for all new backup service logic |

---

## Task 1: Settings Model — New Destination Fields

**Files:**
- Modify: `app/models/settings.py:152-254`
- Test: `tests/test_backup.py` (create this file)

- [ ] **Step 1: Create `tests/test_backup.py` with failing tests for `get_backup_config` defaults**

```python
"""Tests for app/services/backup.py and backup-related settings."""
import pytest


class TestGetBackupConfigDefaults:
    def test_enable_remote_defaults_to_true(self, app):
        from app.models import Settings
        config = Settings.get_backup_config()
        assert config["enable_remote"] == "true"

    def test_enable_local_defaults_to_false(self, app):
        from app.models import Settings
        config = Settings.get_backup_config()
        assert config["enable_local"] == "false"

    def test_local_path_defaults_to_empty(self, app):
        from app.models import Settings
        config = Settings.get_backup_config()
        assert config["local_path"] == ""


class TestSaveBackupConfigDestinationFields:
    def test_saves_enable_remote(self, app):
        from app.models import Settings
        Settings.save_backup_config(
            host="h", port="22", user="u", remote_path="/r",
            enable_remote="false", enable_local="false", local_path="",
        )
        assert Settings.get("backup_enable_remote") == "false"

    def test_saves_enable_local(self, app):
        from app.models import Settings
        Settings.save_backup_config(
            host="h", port="22", user="u", remote_path="/r",
            enable_remote="false", enable_local="true", local_path="/var/backups",
        )
        assert Settings.get("backup_enable_local") == "true"

    def test_saves_local_path_stripped(self, app):
        from app.models import Settings
        Settings.save_backup_config(
            host="h", port="22", user="u", remote_path="/r",
            enable_remote="false", enable_local="true", local_path="  /var/backups  ",
        )
        assert Settings.get("backup_local_path") == "/var/backups"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest tests/test_backup.py -v 2>&1 | head -40
```

Expected: `AttributeError` or `KeyError` — the keys don't exist yet.

- [ ] **Step 3: Add the three new fields to `get_backup_config()`**

In `app/models/settings.py`, find `get_backup_config` (line ~152) and add three entries to the returned dict:

```python
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
        }
```

- [ ] **Step 4: Update `save_backup_config()` signature and body**

Replace the existing `save_backup_config` method (line ~236):

```python
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
    ) -> None:
        """Persist SCP backup configuration and destination settings.

        If *key_content* is provided it replaces any previously stored key.
        Omit (or pass None) to keep the existing stored key unchanged.
        """
        cls.set("backup_scp_host", host.strip())
        cls.set("backup_scp_port", port.strip() or "22")
        cls.set("backup_scp_user", user.strip())
        cls.set("backup_scp_remote_path", remote_path.strip())
        if key_content is not None:
            cls.set("backup_scp_key_content", key_content)
        cls.set("backup_enable_remote", enable_remote)
        cls.set("backup_enable_local", enable_local)
        cls.set("backup_local_path", local_path.strip())
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest tests/test_backup.py::TestGetBackupConfigDefaults tests/test_backup.py::TestSaveBackupConfigDestinationFields -v
```

Expected: 6 tests, all PASS.

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest --tb=short -q 2>&1 | tail -20
```

Expected: all previously passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add app/models/settings.py tests/test_backup.py
git commit -m "feat: add backup destination settings fields to Settings model"
```

---

## Task 2: Backup Service — Configuration Helpers

**Files:**
- Modify: `app/services/backup.py:57-69` (replace `is_configured`)
- Test: `tests/test_backup.py` (add to existing file)

- [ ] **Step 1: Add failing tests for the three new helper functions**

Append to `tests/test_backup.py`:

```python
class TestIsRemoteConfigured:
    def _set_all_scp(self):
        from app.models import Settings
        Settings.set("backup_scp_host", "host.example.com")
        Settings.set("backup_scp_user", "backupuser")
        Settings.set("backup_scp_key_content", "fake-key")
        Settings.set("backup_scp_remote_path", "/backups")

    def test_true_when_enabled_and_all_scp_present(self, app):
        from app.models import Settings
        from app.services.backup import is_remote_configured
        Settings.set("backup_enable_remote", "true")
        self._set_all_scp()
        assert is_remote_configured() is True

    def test_false_when_disabled(self, app):
        from app.models import Settings
        from app.services.backup import is_remote_configured
        Settings.set("backup_enable_remote", "false")
        self._set_all_scp()
        assert is_remote_configured() is False

    def test_false_when_scp_host_missing(self, app):
        from app.models import Settings
        from app.services.backup import is_remote_configured
        Settings.set("backup_enable_remote", "true")
        # no SCP settings set
        assert is_remote_configured() is False


class TestIsLocalConfigured:
    def test_true_when_enabled_and_path_set(self, app):
        from app.models import Settings
        from app.services.backup import is_local_configured
        Settings.set("backup_enable_local", "true")
        Settings.set("backup_local_path", "/var/backups/mandate")
        assert is_local_configured() is True

    def test_false_when_disabled(self, app):
        from app.models import Settings
        from app.services.backup import is_local_configured
        Settings.set("backup_enable_local", "false")
        Settings.set("backup_local_path", "/var/backups/mandate")
        assert is_local_configured() is False

    def test_false_when_path_empty(self, app):
        from app.models import Settings
        from app.services.backup import is_local_configured
        Settings.set("backup_enable_local", "true")
        # no path set
        assert is_local_configured() is False


class TestIsConfigured:
    def test_true_when_only_local_configured(self, app):
        from app.models import Settings
        from app.services.backup import is_configured
        Settings.set("backup_enable_local", "true")
        Settings.set("backup_local_path", "/var/backups/mandate")
        assert is_configured() is True

    def test_true_when_only_remote_configured(self, app):
        from app.models import Settings
        from app.services.backup import is_configured
        Settings.set("backup_enable_remote", "true")
        Settings.set("backup_scp_host", "host.example.com")
        Settings.set("backup_scp_user", "u")
        Settings.set("backup_scp_key_content", "k")
        Settings.set("backup_scp_remote_path", "/r")
        assert is_configured() is True

    def test_false_when_neither_configured(self, app):
        from app.services.backup import is_configured
        # clean DB — nothing set
        assert is_configured() is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest tests/test_backup.py::TestIsRemoteConfigured tests/test_backup.py::TestIsLocalConfigured tests/test_backup.py::TestIsConfigured -v 2>&1 | head -30
```

Expected: `ImportError` or `AttributeError` — `is_remote_configured` and `is_local_configured` don't exist yet.

- [ ] **Step 3: Replace `is_configured()` in `app/services/backup.py`**

Find the existing `is_configured` function (line ~57) and replace it with three functions:

```python
def is_remote_configured() -> bool:
    """Return True if SFTP backup is enabled and all SCP settings are present."""
    from app.models import Settings

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
```

- [ ] **Step 4: Run the new tests**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest tests/test_backup.py::TestIsRemoteConfigured tests/test_backup.py::TestIsLocalConfigured tests/test_backup.py::TestIsConfigured -v
```

Expected: 9 tests, all PASS.

- [ ] **Step 5: Run full test suite**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest --tb=short -q 2>&1 | tail -20
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/backup.py tests/test_backup.py
git commit -m "feat: replace is_configured with is_remote_configured/is_local_configured"
```

---

## Task 3: Backup Service — Local Save and Retention

**Files:**
- Modify: `app/services/backup.py` (add two new functions after `_apply_retention`)
- Test: `tests/test_backup.py` (add to existing file)

- [ ] **Step 1: Add failing tests for `_apply_local_retention` and `_local_save`**

Append to `tests/test_backup.py`:

```python
import os
import tempfile
from datetime import datetime


class TestApplyLocalRetention:
    def _make_backup_files(self, tmp_path, timestamps):
        """Create empty backup files with the given timestamp strings (YYYYMMDD-HHMMSS)."""
        for ts in timestamps:
            (tmp_path / f"petition-qc-backup-{ts}.dump").touch()

    def test_daily_keeps_seven_most_recent(self, tmp_path):
        from app.services.backup import _apply_local_retention
        # 10 daily files at 10:00 (not 02:00, so no weekly overlap)
        timestamps = [
            datetime(2024, 1, i + 1, 10, 0, 0).strftime("%Y%m%d-%H%M%S")
            for i in range(10)
        ]
        self._make_backup_files(tmp_path, timestamps)

        _apply_local_retention(str(tmp_path), "daily")

        remaining = sorted(f.name for f in tmp_path.iterdir())
        assert len(remaining) == 7
        # The 7 most recent should be kept (Jan 4–10)
        assert "petition-qc-backup-20240110-100000.dump" in remaining
        assert "petition-qc-backup-20240101-100000.dump" not in remaining

    def test_weekly_keeps_four_most_recent(self, tmp_path):
        from app.services.backup import _apply_local_retention
        # 6 Sunday files at 02:00
        timestamps = [
            "20240107-020000",
            "20240114-020000",
            "20240121-020000",
            "20240128-020000",
            "20240204-020000",
            "20240211-020000",
        ]
        self._make_backup_files(tmp_path, timestamps)

        _apply_local_retention(str(tmp_path), "weekly")

        remaining = sorted(f.name for f in tmp_path.iterdir())
        assert len(remaining) == 4
        assert "petition-qc-backup-20240211-020000.dump" in remaining
        assert "petition-qc-backup-20240107-020000.dump" not in remaining

    def test_unknown_schedule_keeps_all(self, tmp_path):
        from app.services.backup import _apply_local_retention
        timestamps = ["20240101-020000", "20240102-020000", "20240103-020000"]
        self._make_backup_files(tmp_path, timestamps)

        _apply_local_retention(str(tmp_path), "")

        assert len(list(tmp_path.iterdir())) == 3

    def test_non_matching_files_are_ignored(self, tmp_path):
        from app.services.backup import _apply_local_retention
        # Only 3 valid backups — the README should not be touched
        self._make_backup_files(tmp_path, [
            "20240101-020000", "20240102-020000", "20240103-020000",
        ])
        (tmp_path / "README.txt").touch()

        _apply_local_retention(str(tmp_path), "daily")

        assert (tmp_path / "README.txt").exists()


class TestLocalSave:
    def test_copies_dump_to_destination(self, tmp_path):
        from app.services.backup import _local_save

        fd, dump_path = tempfile.mkstemp(suffix=".dump")
        os.write(fd, b"fake dump content")
        os.close(fd)
        try:
            _local_save(dump_path, str(tmp_path), schedule="")
            files = list(tmp_path.iterdir())
            assert len(files) == 1
            assert files[0].name.startswith("petition-qc-backup-")
            assert files[0].name.endswith(".dump")
            assert files[0].read_bytes() == b"fake dump content"
        finally:
            os.unlink(dump_path)

    def test_raises_runtime_error_on_bad_directory(self, tmp_path):
        from app.services.backup import _local_save

        fd, dump_path = tempfile.mkstemp(suffix=".dump")
        os.close(fd)
        try:
            with pytest.raises(RuntimeError, match="Local backup copy failed"):
                _local_save(dump_path, "/nonexistent/path/mandate-test", schedule="")
        finally:
            os.unlink(dump_path)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest tests/test_backup.py::TestApplyLocalRetention tests/test_backup.py::TestLocalSave -v 2>&1 | head -20
```

Expected: `ImportError` — `_apply_local_retention` and `_local_save` don't exist yet.

- [ ] **Step 3: Add `_apply_local_retention` to `app/services/backup.py`**

Add this function right after the `_apply_retention` function (after line ~493):

```python
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
```

- [ ] **Step 4: Add `_local_save` to `app/services/backup.py`**

Add this function immediately after `_apply_local_retention`:

```python
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
```

- [ ] **Step 5: Run the new tests**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest tests/test_backup.py::TestApplyLocalRetention tests/test_backup.py::TestLocalSave -v
```

Expected: 6 tests, all PASS.

- [ ] **Step 6: Run full test suite**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest --tb=short -q 2>&1 | tail -20
```

Expected: all previously passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add app/services/backup.py tests/test_backup.py
git commit -m "feat: add _local_save and _apply_local_retention to backup service"
```

---

## Task 4: Backup Service — Independent Destination Execution

**Files:**
- Modify: `app/services/backup.py` — rewrite `_backup_thread`
- Test: `tests/test_backup.py` (add to existing file)

- [ ] **Step 1: Add failing tests for the updated `_backup_thread` behavior**

Append to `tests/test_backup.py`:

```python
from unittest.mock import patch, MagicMock


class TestBackupThread:
    """Tests for _backup_thread independent-destination behavior."""

    def _run_thread(self, app, remote_ok, local_ok, remote_raises=None, local_raises=None):
        """Helper: run _backup_thread with mocked destinations."""
        from app.services.backup import _backup_thread

        sftp_mock = MagicMock(side_effect=remote_raises) if remote_raises else MagicMock()
        local_mock = MagicMock(side_effect=local_raises) if local_raises else MagicMock()

        with patch("app.services.backup.is_remote_configured", return_value=remote_ok), \
             patch("app.services.backup.is_local_configured", return_value=local_ok), \
             patch("app.services.backup._create_pg_dump", return_value="/tmp/fake.dump"), \
             patch("app.services.backup._sftp_upload", sftp_mock), \
             patch("app.services.backup._local_save", local_mock), \
             patch("app.services.backup._send_backup_notification"), \
             patch("os.path.exists", return_value=True), \
             patch("os.unlink"):
            _backup_thread(app)

        return sftp_mock, local_mock

    def test_both_succeed_sets_success_status(self, app):
        from app.models import Settings
        self._run_thread(app, remote_ok=True, local_ok=True)
        assert Settings.get("backup_last_status") == "success"

    def test_remote_fails_local_still_runs(self, app):
        from app.models import Settings
        _, local_mock = self._run_thread(
            app, remote_ok=True, local_ok=True,
            remote_raises=RuntimeError("SFTP timeout"),
        )
        local_mock.assert_called_once()
        status = Settings.get("backup_last_status")
        assert status.startswith("error:")
        assert "remote" in status

    def test_local_fails_remote_still_runs(self, app):
        from app.models import Settings
        sftp_mock, _ = self._run_thread(
            app, remote_ok=True, local_ok=True,
            local_raises=RuntimeError("disk full"),
        )
        sftp_mock.assert_called_once()
        status = Settings.get("backup_last_status")
        assert status.startswith("error:")
        assert "local" in status

    def test_only_remote_configured_skips_local(self, app):
        from app.models import Settings
        sftp_mock, local_mock = self._run_thread(app, remote_ok=True, local_ok=False)
        sftp_mock.assert_called_once()
        local_mock.assert_not_called()
        assert Settings.get("backup_last_status") == "success"

    def test_only_local_configured_skips_remote(self, app):
        from app.models import Settings
        sftp_mock, local_mock = self._run_thread(app, remote_ok=False, local_ok=True)
        sftp_mock.assert_not_called()
        local_mock.assert_called_once()
        assert Settings.get("backup_last_status") == "success"

    def test_pgdump_failure_sets_error_status(self, app):
        from app.models import Settings
        from app.services.backup import _backup_thread

        with patch("app.services.backup.is_remote_configured", return_value=True), \
             patch("app.services.backup.is_local_configured", return_value=False), \
             patch("app.services.backup._create_pg_dump",
                   side_effect=RuntimeError("pg_dump crashed")), \
             patch("app.services.backup._send_backup_notification"), \
             patch("os.path.exists", return_value=False), \
             patch("os.unlink"):
            _backup_thread(app)

        status = Settings.get("backup_last_status")
        assert status.startswith("error:")
        assert "pg_dump crashed" in status
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest tests/test_backup.py::TestBackupThread -v 2>&1 | head -30
```

Expected: FAIL — current `_backup_thread` doesn't call `is_remote_configured`/`is_local_configured` or collect errors independently.

- [ ] **Step 3: Rewrite `_backup_thread` in `app/services/backup.py`**

Replace the entire `_backup_thread` function (line ~96):

```python
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

        try:
            version_num = db.session.execute(
                text("SHOW server_version_num")
            ).scalar()
            server_major = int(version_num) // 10000
        except Exception:
            server_major = None

        schedule = Settings.get("backup_schedule", "")

        dump_file = None
        errors: list[str] = []
        try:
            dump_file = _create_pg_dump(db_url, server_major)

            if is_remote_configured():
                try:
                    _sftp_upload(dump_file, scp_config, schedule=schedule)
                except Exception as exc:
                    logger.exception("Remote backup failed")
                    errors.append(f"remote: {str(exc)[:200]}")

            if is_local_configured():
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
```

- [ ] **Step 4: Run the new tests**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest tests/test_backup.py::TestBackupThread -v
```

Expected: 6 tests, all PASS.

- [ ] **Step 5: Run full test suite**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest --tb=short -q 2>&1 | tail -20
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/backup.py tests/test_backup.py
git commit -m "feat: rewrite _backup_thread for independent destination execution"
```

---

## Task 5: Route — Parse New Form Fields

**Files:**
- Modify: `app/routes/settings.py` — `save_backup_config` and `test_backup_connection` routes
- Test: `tests/test_backup.py` (add route test)

- [ ] **Step 1: Add a failing route test**

Append to `tests/test_backup.py`:

```python
class TestSaveBackupConfigRoute:
    def test_stores_destination_settings(self, client, app):
        from app.models import Settings
        from app import db
        from tests.conftest import make_user, login

        admin = make_user(role="admin")
        db.session.commit()
        login(client, admin)

        resp = client.post("/settings/save-backup-config", data={
            "backup_enable_remote": "1",
            "backup_enable_local": "1",
            "backup_local_path": "/var/backups/mandate",
            "scp_host": "backup.example.com",
            "scp_port": "22",
            "scp_user": "backupuser",
            "scp_remote_path": "/backups",
            "backup_schedule": "daily",
            "backup_notify_success": "",
        }, follow_redirects=False)

        assert resp.status_code == 302
        assert Settings.get("backup_enable_remote") == "true"
        assert Settings.get("backup_enable_local") == "true"
        assert Settings.get("backup_local_path") == "/var/backups/mandate"

    def test_unchecked_remote_stores_false(self, client, app):
        from app.models import Settings
        from app import db
        from tests.conftest import make_user, login

        admin = make_user(role="admin")
        db.session.commit()
        login(client, admin)

        # backup_enable_remote not in form data (checkbox unchecked)
        client.post("/settings/save-backup-config", data={
            "backup_enable_local": "1",
            "backup_local_path": "/var/backups",
            "scp_host": "",
            "scp_port": "22",
            "scp_user": "",
            "scp_remote_path": "",
            "backup_schedule": "",
            "backup_notify_success": "",
        }, follow_redirects=False)

        assert Settings.get("backup_enable_remote") == "false"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest tests/test_backup.py::TestSaveBackupConfigRoute -v 2>&1 | head -20
```

Expected: FAIL — the route doesn't read `backup_enable_remote` / `backup_enable_local` / `backup_local_path` yet.

- [ ] **Step 3: Update `save_backup_config` route in `app/routes/settings.py`**

Find the `save_backup_config` route (line ~108). Replace the `Settings.save_backup_config(...)` call and the lines just before it:

```python
    enable_remote = "true" if request.form.get("backup_enable_remote") else "false"
    enable_local = "true" if request.form.get("backup_enable_local") else "false"
    local_path = request.form.get("backup_local_path", "").strip()

    Settings.save_backup_config(
        host=request.form.get("scp_host", ""),
        port=request.form.get("scp_port", "22"),
        user=request.form.get("scp_user", ""),
        remote_path=request.form.get("scp_remote_path", ""),
        key_content=key_content,
        enable_remote=enable_remote,
        enable_local=enable_local,
        local_path=local_path,
    )
```

- [ ] **Step 4: Fix `test_backup_connection` route to use `is_remote_configured()`**

In `app/routes/settings.py`, find `test_backup_connection` (line ~155). Replace:

```python
        if not backup_service.is_configured():
            return jsonify(ok=False, message="Backup is not fully configured.")
```

With:

```python
        if not backup_service.is_remote_configured():
            return jsonify(ok=False, message="SFTP backup is not fully configured.")
```

- [ ] **Step 5: Run the route tests**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest tests/test_backup.py::TestSaveBackupConfigRoute -v
```

Expected: 2 tests, all PASS.

- [ ] **Step 6: Run full test suite**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest --tb=short -q 2>&1 | tail -20
```

Expected: all previously passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add app/routes/settings.py tests/test_backup.py
git commit -m "feat: parse backup destination fields in save-backup-config route"
```

---

## Task 6: Template — Checkboxes, Local Path Field, JS

**Files:**
- Modify: `app/templates/settings/index.html`

No automated tests — verify manually by starting the dev server and exercising the form.

- [ ] **Step 1: Update the backup section description (line ~291)**

Replace:
```html
            <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
                Backs up all tables except the voter file via SCP/SFTP to a remote server.
                The backup is in PostgreSQL custom format and can be restored with
                <code class="font-mono bg-gray-100 dark:bg-gray-700 px-1 rounded">pg_restore</code>.
            </p>
```

With:
```html
            <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">
                Backs up all tables except the voter file. Supports remote SFTP upload, local
                filesystem storage, or both. The backup is in PostgreSQL custom format and can
                be restored with <code class="font-mono bg-gray-100 dark:bg-gray-700 px-1 rounded">pg_restore</code>.
            </p>
```

- [ ] **Step 2: Add `id="backup-config-form"` to the form element (line ~366)**

Replace:
```html
        <form method="POST" action="{{ url_for('settings.save_backup_config') }}"
              enctype="multipart/form-data"
              class="space-y-4 pt-4 border-t border-gray-200 dark:border-gray-700">
```

With:
```html
        <form id="backup-config-form" method="POST" action="{{ url_for('settings.save_backup_config') }}"
              enctype="multipart/form-data"
              class="space-y-4 pt-4 border-t border-gray-200 dark:border-gray-700">
```

- [ ] **Step 3: Add destination checkboxes and error element before the SCP heading**

Replace:
```html
            <h3 class="text-sm font-semibold text-gray-700 dark:text-gray-300">SCP / SFTP Configuration</h3>
```

With:
```html
            <!-- Destination selection -->
            <div class="space-y-2">
                <p class="text-sm font-medium text-gray-700 dark:text-gray-300">Backup Destinations</p>
                <div class="flex flex-col gap-2">
                    <div class="flex items-center gap-2">
                        <input type="checkbox" name="backup_enable_remote" id="backup_enable_remote" value="1"
                               {% if backup_config.enable_remote == 'true' %}checked{% endif %}
                               onchange="updateBackupDestinationVisibility()"
                               class="rounded border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-navy-600">
                        <label for="backup_enable_remote" class="text-sm font-medium text-gray-700 dark:text-gray-300">
                            Upload via SFTP
                        </label>
                    </div>
                    <div class="flex items-center gap-2">
                        <input type="checkbox" name="backup_enable_local" id="backup_enable_local" value="1"
                               {% if backup_config.enable_local == 'true' %}checked{% endif %}
                               onchange="updateBackupDestinationVisibility()"
                               class="rounded border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-navy-600">
                        <label for="backup_enable_local" class="text-sm font-medium text-gray-700 dark:text-gray-300">
                            Save to local directory
                        </label>
                    </div>
                </div>
                <p id="destination-error" class="text-sm text-red-600 dark:text-red-400 hidden">
                    At least one backup destination must be enabled when a schedule is set.
                </p>
            </div>

            <!-- SFTP fields (hidden when SFTP unchecked) -->
            <div id="sftp-config-section">
            <h3 class="text-sm font-semibold text-gray-700 dark:text-gray-300">SCP / SFTP Configuration</h3>
```

- [ ] **Step 4: Close the `sftp-config-section` div after the Remote Directory field**

Find the closing `</div>` that ends the Remote Directory field block (after `scp_remote_path` input, line ~443). Add a closing `</div>` for `sftp-config-section` immediately after it:

```html
                <input type="text" name="scp_remote_path" id="scp_remote_path"
                       value="{{ backup_config.scp_remote_path }}"
                       placeholder="/backups/petition-qc"
                       class="block w-full rounded-md bg-gray-50 dark:bg-gray-700 border-gray-300 dark:border-gray-600 text-gray-900 dark:text-white px-3 py-2 text-sm font-mono focus:border-navy-500 focus:ring-navy-500">
            </div>
            </div><!-- end sftp-config-section -->
```

- [ ] **Step 5: Add the local directory field after `sftp-config-section`**

Immediately after the `</div><!-- end sftp-config-section -->` line, add:

```html
            <!-- Local directory field (hidden when local unchecked) -->
            <div id="local-config-section">
                <div>
                    <label for="backup_local_path" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        Local Directory
                    </label>
                    <p class="text-xs text-gray-500 dark:text-gray-400 mb-1">
                        Absolute path on the server where backup files will be written. Must exist and be writable.
                    </p>
                    <input type="text" name="backup_local_path" id="backup_local_path"
                           value="{{ backup_config.local_path }}"
                           placeholder="/var/backups/mandate"
                           class="block w-full rounded-md bg-gray-50 dark:bg-gray-700 border-gray-300 dark:border-gray-600 text-gray-900 dark:text-white px-3 py-2 text-sm font-mono focus:border-navy-500 focus:ring-navy-500">
                </div>
            </div><!-- end local-config-section -->
```

- [ ] **Step 6: Add JS functions at the bottom of the page (before the closing `</script>` tag)**

Find the existing `</script>` closing tag near the bottom of the page and add these functions just before it:

```javascript
function updateBackupDestinationVisibility() {
    const remoteEnabled = document.getElementById('backup_enable_remote').checked;
    const localEnabled = document.getElementById('backup_enable_local').checked;
    document.getElementById('sftp-config-section').style.display = remoteEnabled ? '' : 'none';
    document.getElementById('local-config-section').style.display = localEnabled ? '' : 'none';
}

document.getElementById('backup-config-form').addEventListener('submit', function (e) {
    const schedule = document.getElementById('backup_schedule').value;
    const remoteEnabled = document.getElementById('backup_enable_remote').checked;
    const localEnabled = document.getElementById('backup_enable_local').checked;
    const err = document.getElementById('destination-error');
    if (schedule && !remoteEnabled && !localEnabled) {
        e.preventDefault();
        err.classList.remove('hidden');
        err.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } else {
        err.classList.add('hidden');
    }
});

// Apply initial visibility on page load
updateBackupDestinationVisibility();
```

- [ ] **Step 7: Start the dev server and verify manually**

```bash
cd /home/adam/Projects/Mandate && python run.py
```

Open `http://localhost:5000/settings#backup`. Verify:

1. Both checkboxes are visible; "Upload via SFTP" is checked by default for new installs (default `enable_remote = "true"`).
2. Unchecking "Upload via SFTP" hides the SFTP credential fields; checking it shows them again.
3. Checking "Save to local directory" reveals the Local Directory input; unchecking hides it.
4. Setting a schedule and unchecking both destinations then clicking "Save Backup Config" shows the inline error and does NOT submit.
5. Checking at least one destination with a schedule set → form submits and the settings page reloads with a success flash.
6. Reloading the page shows the saved checkbox state correctly.

- [ ] **Step 8: Run full test suite one final time**

```bash
cd /home/adam/Projects/Mandate && .venv/bin/pytest --tb=short -q 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add app/templates/settings/index.html
git commit -m "feat: add SFTP/local destination checkboxes to backup settings UI"
```

"""Tests for app/services/backup.py and backup-related settings."""
import os
import tempfile
from datetime import datetime

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
        assert Settings.get_backup_config()["enable_remote"] == "false"

    def test_saves_enable_local(self, app):
        from app.models import Settings
        Settings.save_backup_config(
            host="h", port="22", user="u", remote_path="/r",
            enable_remote="false", enable_local="true", local_path="/var/backups",
        )
        assert Settings.get_backup_config()["enable_local"] == "true"

    def test_saves_local_path_stripped(self, app):
        from app.models import Settings
        Settings.save_backup_config(
            host="h", port="22", user="u", remote_path="/r",
            enable_remote="false", enable_local="true", local_path="  /var/backups  ",
        )
        assert Settings.get_backup_config()["local_path"] == "/var/backups"

    def test_saves_enable_remote_from_checkbox_value(self, app):
        from app.models import Settings
        Settings.save_backup_config(
            host="h", port="22", user="u", remote_path="/r",
            enable_remote="1", enable_local="false", local_path="",
        )
        assert Settings.get_backup_config()["enable_remote"] == "true"


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

    def test_false_when_one_scp_field_missing(self, app):
        from app.models import Settings
        from app.services.backup import is_remote_configured
        Settings.set("backup_enable_remote", "true")
        Settings.set("backup_scp_host", "host.example.com")
        Settings.set("backup_scp_user", "user")
        Settings.set("backup_scp_key_content", "fake-key")
        # backup_scp_remote_path intentionally omitted
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

    def test_false_when_path_is_whitespace_only(self, app):
        from app.models import Settings
        from app.services.backup import is_local_configured
        Settings.set("backup_enable_local", "true")
        Settings.set("backup_local_path", "   ")
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
             patch("app.services.backup.os.path.exists", return_value=True), \
             patch("app.services.backup.os.unlink"):
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
             patch("app.services.backup.os.path.exists", return_value=False), \
             patch("app.services.backup.os.unlink"):
            _backup_thread(app)

        status = Settings.get("backup_last_status")
        assert status.startswith("error:")
        assert "pg_dump crashed" in status

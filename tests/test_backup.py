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

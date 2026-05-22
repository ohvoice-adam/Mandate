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

"""Google Drive backup: settings, backup-thread integration, and admin routes."""
import io
import json
from unittest.mock import MagicMock, patch

from tests.test_backup_gdrive import FOLDER, SA_EMAIL, VALID_KEY


def _enable_gdrive(key: str | None = VALID_KEY, folder: str = FOLDER, enabled: str = "true") -> None:
    from app.models import Settings
    Settings.set("backup_enable_gdrive", enabled)
    if key is not None:
        Settings.set("backup_gdrive_sa_json", key)
    Settings.set("backup_gdrive_folder_id", folder)


class TestIsGdriveConfigured:
    def test_default_is_not_configured(self, app):
        from app.services.backup import is_gdrive_configured
        assert is_gdrive_configured() is False

    def test_disabled_flag_wins(self, app):
        from app.services.backup import is_gdrive_configured
        _enable_gdrive(enabled="false")
        assert is_gdrive_configured() is False

    def test_missing_folder(self, app):
        from app.services.backup import is_gdrive_configured
        _enable_gdrive(folder="")
        assert is_gdrive_configured() is False

    def test_missing_key(self, app):
        from app.services.backup import is_gdrive_configured
        _enable_gdrive(key=None)
        assert is_gdrive_configured() is False

    def test_fully_configured_counts_as_configured(self, app):
        from app.models import Settings
        from app.services.backup import is_configured, is_gdrive_configured
        Settings.set("backup_enable_remote", "false")
        _enable_gdrive()
        assert is_gdrive_configured() is True
        assert is_configured() is True

    def test_backup_config_exposes_client_email_not_key(self, app):
        from app.models import Settings
        _enable_gdrive()
        config = Settings.get_backup_config()
        assert config["gdrive_client_email"] == SA_EMAIL
        assert config["has_gdrive_key"] is True
        assert VALID_KEY not in json.dumps(config)


class TestBackupThreadGdrive:
    def _run(self, app, remote_ok, gdrive_raises=None):
        from app.services.backup import _backup_thread
        sftp_mock = MagicMock()
        gdrive_mock = MagicMock(side_effect=gdrive_raises) if gdrive_raises else MagicMock()
        with patch("app.services.backup.is_remote_configured", return_value=remote_ok), \
             patch("app.services.backup.is_local_configured", return_value=False), \
             patch("app.services.backup.is_gdrive_configured", return_value=True), \
             patch("app.services.backup._create_pg_dump", return_value="/tmp/fake.dump"), \
             patch("app.services.backup._sftp_upload", sftp_mock), \
             patch("app.services.backup._gdrive_upload", gdrive_mock), \
             patch("app.services.backup._send_backup_notification"), \
             patch("app.services.backup.os.path.exists", return_value=True), \
             patch("app.services.backup.os.unlink"):
            _backup_thread(app)
        return sftp_mock, gdrive_mock

    def test_gdrive_only_success(self, app):
        from app.models import Settings
        _enable_gdrive()
        _, gdrive_mock = self._run(app, remote_ok=False)
        gdrive_mock.assert_called_once()
        assert gdrive_mock.call_args.args[1:] == (VALID_KEY, FOLDER)
        assert Settings.get("backup_last_status") == "success"

    def test_gdrive_failure_does_not_block_sftp(self, app):
        from app.models import Settings
        _enable_gdrive()
        sftp_mock, _ = self._run(app, remote_ok=True, gdrive_raises=RuntimeError("quota"))
        sftp_mock.assert_called_once()
        status = Settings.get("backup_last_status")
        assert status.startswith("error:")
        assert "gdrive: quota" in status
        assert "remote" not in status


def _login_admin(client):
    from app import db
    from tests.conftest import login, make_user
    admin = make_user(role="admin")
    db.session.commit()
    login(client, admin)


def _form(**extra):
    data = {
        "scp_host": "", "scp_port": "22", "scp_user": "", "scp_remote_path": "",
        "backup_schedule": "", "backup_notify_success": "",
    }
    data.update(extra)
    return data


class TestSaveGdriveRoute:
    def test_stores_key_and_extracts_folder_id_from_url(self, client, app):
        from app.models import Settings
        _login_admin(client)
        resp = client.post("/settings/save-backup-config", data=_form(
            backup_enable_gdrive="1",
            gdrive_folder_id=f"https://drive.google.com/drive/folders/{FOLDER}?usp=sharing",
            gdrive_key_file=(io.BytesIO(VALID_KEY.encode()), "key.json"),
        ), content_type="multipart/form-data")
        assert resp.status_code == 302
        assert Settings.get("backup_enable_gdrive") == "true"
        assert Settings.get("backup_gdrive_folder_id") == FOLDER
        assert Settings.get("backup_gdrive_sa_json") == VALID_KEY

    def test_invalid_key_file_rejected_and_not_stored(self, client, app):
        from app.models import Settings
        _login_admin(client)
        resp = client.post("/settings/save-backup-config", data=_form(
            backup_enable_gdrive="1",
            gdrive_folder_id=FOLDER,
            gdrive_key_file=(io.BytesIO(b'{"type": "authorized_user"}'), "key.json"),
        ), content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Google key file rejected" in resp.data
        assert not Settings.get("backup_gdrive_sa_json")
        assert Settings.get("backup_enable_gdrive", "false") == "false"

    def test_saving_without_new_key_keeps_existing_key(self, client, app):
        from app.models import Settings
        _enable_gdrive()
        _login_admin(client)
        client.post("/settings/save-backup-config", data=_form(
            backup_enable_gdrive="1", gdrive_folder_id=FOLDER,
        ))
        assert Settings.get("backup_gdrive_sa_json") == VALID_KEY

    def test_key_excluded_from_config_export(self, client, app):
        _enable_gdrive()
        _login_admin(client)
        resp = client.get("/settings/export-config")
        assert resp.status_code == 200
        exported = resp.get_json()["mandate_settings"]
        assert "backup_gdrive_sa_json" not in exported
        assert exported["backup_gdrive_folder_id"] == FOLDER


class TestGdriveConnectionRoute:
    def test_reports_unconfigured(self, client, app):
        _login_admin(client)
        resp = client.post("/settings/test-gdrive-connection")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is False

    def test_passes_through_service_result(self, client, app):
        _enable_gdrive()
        _login_admin(client)
        with patch("app.services.backup_gdrive.test_connection",
                   return_value=(True, "Connected.")) as tc:
            resp = client.post("/settings/test-gdrive-connection")
        assert resp.get_json() == {"ok": True, "message": "Connected."}
        tc.assert_called_once_with(VALID_KEY, FOLDER)

    def test_requires_admin(self, client, app):
        from app import db
        from tests.conftest import login, make_user
        user = make_user(role="organizer")
        db.session.commit()
        login(client, user)
        resp = client.post("/settings/test-gdrive-connection")
        assert resp.status_code in (302, 403)


class TestGdrivePagesRender:
    def test_settings_page_shows_drive_section_and_share_email(self, client, app):
        _enable_gdrive()
        _login_admin(client)
        resp = client.get("/settings/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="backup_enable_gdrive"' in html
        assert 'id="gdrive-config-section"' in html
        assert SA_EMAIL in html
        assert 'id="test-gdrive-btn"' in html
        assert "BEGIN PRIVATE KEY" not in html

    def test_health_page_counts_drive_only_as_configured(self, client, app):
        from app.models import Settings
        Settings.set("backup_enable_remote", "false")
        _enable_gdrive()
        _login_admin(client)
        resp = client.get("/settings/system-health")
        assert resp.status_code == 200
        assert b"Backup configured, never run." in resp.data


def test_real_google_credentials_build_from_key():
    """_session builds genuine google-auth credentials (no network call)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app.services import backup_gdrive as gd

    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    key = json.dumps({
        "type": "service_account", "client_email": SA_EMAIL, "private_key": pem,
        "token_uri": "https://oauth2.googleapis.com/token", "project_id": "p",
    })
    session = gd._session(key)
    assert session.credentials.service_account_email == SA_EMAIL
    assert gd.DRIVE_SCOPE in session.credentials.scopes

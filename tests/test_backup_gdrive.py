"""Unit tests for app/services/backup_gdrive.py (all Google HTTP calls mocked)."""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.services import backup_gdrive as gd

SA_EMAIL = "mandate-backup@proj.iam.gserviceaccount.com"
VALID_KEY = json.dumps({
    "type": "service_account",
    "client_email": SA_EMAIL,
    "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
})
FOLDER = "1AbCdEfGhIjKlMnOp"


def _resp(status: int = 200, body: dict | None = None, headers: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    r.headers = headers or {}
    r.text = json.dumps(body or {})
    return r


class TestParseServiceAccount:
    def test_valid_key_returns_client_email(self):
        assert gd.parse_service_account(VALID_KEY)["client_email"] == SA_EMAIL

    def test_invalid_json_rejected(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            gd.parse_service_account("{not json")

    def test_wrong_type_rejected(self):
        key = json.dumps({"type": "authorized_user", "client_email": "a", "private_key": "b"})
        with pytest.raises(ValueError, match="service-account"):
            gd.parse_service_account(key)

    def test_missing_private_key_rejected(self):
        key = json.dumps({"type": "service_account", "client_email": SA_EMAIL})
        with pytest.raises(ValueError, match="private_key"):
            gd.parse_service_account(key)

    def test_client_email_helper_tolerates_bad_input(self):
        assert gd.client_email(None) == ""
        assert gd.client_email("garbage") == ""
        assert gd.client_email(VALID_KEY) == SA_EMAIL


class TestNormalizeFolderId:
    @pytest.mark.parametrize("raw, expected", [
        (FOLDER, FOLDER),
        (f"  {FOLDER}  ", FOLDER),
        (f"https://drive.google.com/drive/folders/{FOLDER}", FOLDER),
        (f"https://drive.google.com/drive/u/0/folders/{FOLDER}?usp=sharing", FOLDER),
        ("", ""),
        ("not a folder id!", ""),
    ])
    def test_normalize(self, raw, expected):
        assert gd.normalize_folder_id(raw) == expected


class TestUpload:
    def test_uploads_to_shared_drive_folder_with_backup_name(self, tmp_path):
        dump = tmp_path / "x.dump"
        dump.write_bytes(b"PGDMP-data")
        session = MagicMock()
        session.post.return_value = _resp(200, headers={"Location": "https://upload/session"})
        session.put.return_value = _resp(200, {"id": "file123"})

        with patch.object(gd, "_session", return_value=session), \
             patch.object(gd, "_apply_retention") as retention:
            file_id = gd.upload(str(dump), VALID_KEY, FOLDER, schedule="daily")

        assert file_id == "file123"
        _, kwargs = session.post.call_args
        assert kwargs["params"]["supportsAllDrives"] == "true"
        assert kwargs["params"]["uploadType"] == "resumable"
        assert kwargs["json"]["parents"] == [FOLDER]
        from app.services.backup_retention import BACKUP_RE as _BACKUP_RE
        assert _BACKUP_RE.match(kwargs["json"]["name"])
        assert kwargs["headers"]["X-Upload-Content-Length"] == str(len(b"PGDMP-data"))
        assert session.put.call_args[0][0] == "https://upload/session"
        retention.assert_called_once_with(session, FOLDER, "daily")

    def test_http_error_raises_with_google_message(self, tmp_path):
        dump = tmp_path / "x.dump"
        dump.write_bytes(b"x")
        session = MagicMock()
        session.post.return_value = _resp(403, {"error": {"message": "Insufficient permissions"}})

        with patch.object(gd, "_session", return_value=session), \
             pytest.raises(gd.DriveError, match="Insufficient permissions"):
            gd.upload(str(dump), VALID_KEY, FOLDER)
        session.put.assert_not_called()


class TestRetention:
    def _files(self, names):
        return [{"id": f"id-{n}", "name": n} for n in names]

    def test_weekly_trashes_only_older_backups(self):
        names = [f"petition-qc-backup-2026010{d}-020000.dump" for d in range(1, 7)]
        names.append("notes.txt")
        session = MagicMock()
        session.get.return_value = _resp(200, {"files": self._files(names)})
        session.patch.return_value = _resp(200, {})

        gd._apply_retention(session, FOLDER, "weekly")

        trashed = {c.args[0].rsplit("/", 1)[1] for c in session.patch.call_args_list}
        # Newest 4 kept → 01 and 02 trashed; non-backup file untouched.
        assert trashed == {
            "id-petition-qc-backup-20260101-020000.dump",
            "id-petition-qc-backup-20260102-020000.dump",
        }
        for call in session.patch.call_args_list:
            assert call.kwargs["json"] == {"trashed": True}

    def test_follows_pagination(self):
        session = MagicMock()
        session.get.side_effect = [
            _resp(200, {"files": self._files(["a"]), "nextPageToken": "t2"}),
            _resp(200, {"files": self._files(["b"])}),
        ]
        files = gd._list_folder(session, FOLDER)
        assert [f["name"] for f in files] == ["a", "b"]
        assert session.get.call_args_list[1].kwargs["params"]["pageToken"] == "t2"

    def test_no_schedule_skips_retention(self):
        session = MagicMock()
        gd._apply_retention(session, FOLDER, "")
        session.get.assert_not_called()


class TestConnection:
    def _run(self, resp):
        session = MagicMock()
        session.get.return_value = resp
        with patch.object(gd, "_session", return_value=session):
            return gd.test_connection(VALID_KEY, FOLDER)

    def test_success_names_folder(self):
        ok, msg = self._run(_resp(200, {
            "name": "Backups", "mimeType": gd._FOLDER_MIME, "driveId": "d1",
            "capabilities": {"canAddChildren": True},
        }))
        assert ok is True
        assert "Backups" in msg

    def test_not_found_tells_admin_who_to_share_with(self):
        ok, msg = self._run(_resp(404, {"error": {"message": "File not found"}}))
        assert ok is False
        assert SA_EMAIL in msg

    def test_my_drive_folder_rejected(self):
        ok, msg = self._run(_resp(200, {
            "name": "Mine", "mimeType": gd._FOLDER_MIME,
            "capabilities": {"canAddChildren": True},
        }))
        assert ok is False
        assert "Shared Drive" in msg

    def test_read_only_access_rejected(self):
        ok, msg = self._run(_resp(200, {
            "name": "RO", "mimeType": gd._FOLDER_MIME, "driveId": "d1",
            "capabilities": {"canAddChildren": False},
        }))
        assert ok is False
        assert "Content manager" in msg

    def test_not_a_folder_rejected(self):
        ok, msg = self._run(_resp(200, {"name": "f.txt", "mimeType": "text/plain", "driveId": "d1"}))
        assert ok is False
        assert "not a folder" in msg

    def test_bad_key_reported_without_network(self):
        ok, msg = gd.test_connection("{}", FOLDER)
        assert ok is False
        assert "service-account" in msg

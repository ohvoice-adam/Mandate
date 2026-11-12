"""Backup filenames follow the organization name; retention covers old and new names."""
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.services.backup_retention import (
    LEGACY_PREFIX,
    BackupNaming,
    apply_local_retention,
    local_save,
    parse_backup_dt,
    slugify_prefix,
)


class TestSlugifyPrefix:
    @pytest.mark.parametrize("org_name, expected", [
        ("Ohio Voice", "ohio-voice"),
        ("Café Nuñez", "cafe-nunez"),
        ("  Friends of the Park!! ", "friends-of-the-park"),
        ("../etc/passwd", "etc-passwd"),
        ("A/B\\C.D", "a-b-c-d"),
        ("", LEGACY_PREFIX),
        ("   ", LEGACY_PREFIX),
        ("!!!", LEGACY_PREFIX),
        (None, LEGACY_PREFIX),
    ])
    def test_slug(self, org_name, expected):
        assert slugify_prefix(org_name) == expected

    def test_long_name_capped_without_trailing_dash(self):
        slug = slugify_prefix("Committee " * 10)
        assert len(slug) <= 40
        assert not slug.endswith("-")
        assert slug.startswith("committee-committee")

    def test_only_filename_safe_characters(self):
        slug = slugify_prefix("Weird / name: with * chars? and ..")
        assert set(slug) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")


class TestBackupNaming:
    WHEN = datetime(2026, 11, 12, 2, 0, 0)

    def test_org_name_used_as_prefix(self):
        naming = BackupNaming.for_org("Ohio Voice")
        assert naming.filename(self.WHEN) == "ohio-voice-backup-20261112-020000.dump"
        assert naming.prefixes == {"ohio-voice", LEGACY_PREFIX}

    def test_default_is_legacy(self):
        assert BackupNaming().filename(self.WHEN) == "petition-qc-backup-20261112-020000.dump"
        assert BackupNaming.for_org("").prefix == LEGACY_PREFIX

    def test_org_name_containing_backup_round_trips(self):
        naming = BackupNaming.for_org("Backup Alliance")
        name = naming.filename(self.WHEN)
        assert name == "backup-alliance-backup-20261112-020000.dump"
        assert parse_backup_dt(name, naming.prefixes) == self.WHEN

    def test_from_settings_reads_org_name(self, app):
        from app.models import Settings
        Settings.set("branding_org_name", "Ohio Voice")
        assert BackupNaming.from_settings().prefix == "ohio-voice"
        Settings.set("branding_org_name", "")
        assert BackupNaming.from_settings().prefix == LEGACY_PREFIX


class TestRetentionPrefixes:
    def _make(self, tmp_path, names):
        for n in names:
            (tmp_path / n).touch()

    def test_pools_legacy_and_new_and_ignores_strangers(self, tmp_path):
        legacy = [f"petition-qc-backup-2026010{d}-020000.dump" for d in (1, 2, 3)]
        new = [f"ohio-voice-backup-2026010{d}-020000.dump" for d in (4, 5, 6)]
        strangers = [
            "other-app-backup-20250101-020000.dump",       # another app sharing the folder
            "old-org-name-backup-20250101-020000.dump",    # previous org name
            "notes.txt",
        ]
        self._make(tmp_path, legacy + new + strangers)

        apply_local_retention(str(tmp_path), "weekly", BackupNaming.for_org("Ohio Voice").prefixes)

        remaining = set(os.listdir(tmp_path))
        # Weekly keeps the 4 newest across BOTH prefixes: 03 (legacy) + 04-06 (new).
        assert remaining >= {legacy[2], *new}
        assert legacy[0] not in remaining and legacy[1] not in remaining
        # Files that aren't ours are never touched.
        assert remaining >= set(strangers)

    def test_legacy_only_default_leaves_org_backups_alone(self, tmp_path):
        names = [f"ohio-voice-backup-2026010{d}-020000.dump" for d in range(1, 7)]
        self._make(tmp_path, names)
        apply_local_retention(str(tmp_path), "weekly")
        assert set(os.listdir(tmp_path)) == set(names)

    def test_local_save_uses_given_filename(self, tmp_path):
        dump = tmp_path / "src.dump"
        dump.write_bytes(b"PGDMP")
        dest = tmp_path / "out"
        dest.mkdir()
        local_save(str(dump), str(dest), filename="ohio-voice-backup-20261112-020000.dump")
        assert os.listdir(dest) == ["ohio-voice-backup-20261112-020000.dump"]
        assert (dest / "ohio-voice-backup-20261112-020000.dump").read_bytes() == b"PGDMP"


class TestOneNamePerRun:
    def test_every_destination_gets_the_same_org_filename(self, app):
        from app.models import Settings
        from app.services.backup import _send_to_destinations

        Settings.set("branding_org_name", "Ohio Voice")
        mocks = {n: MagicMock() for n in ("_sftp_upload", "_local_save", "_gdrive_upload")}
        with patch("app.services.backup.is_remote_configured", return_value=True), \
             patch("app.services.backup.is_local_configured", return_value=True), \
             patch("app.services.backup.is_gdrive_configured", return_value=True), \
             patch.multiple("app.services.backup", **mocks):
            errors = _send_to_destinations("/tmp/fake.dump", "daily")

        assert errors == []
        filenames = {m.call_args.kwargs["filename"] for m in mocks.values()}
        assert len(filenames) == 1
        (filename,) = filenames
        assert filename.startswith("ohio-voice-backup-")
        for m in mocks.values():
            assert m.call_args.kwargs["prefixes"] == {"ohio-voice", LEGACY_PREFIX}
            assert m.call_args.kwargs["schedule"] == "daily"

    def test_local_backup_end_to_end_named_after_org(self, app, tmp_path):
        from app.models import Settings
        from app.services.backup import _send_to_destinations

        Settings.set("branding_org_name", "Ohio Voice")
        Settings.set("backup_enable_remote", "false")
        Settings.set("backup_enable_local", "true")
        Settings.set("backup_local_path", str(tmp_path))
        dump = tmp_path / "src.dump"
        dump.write_bytes(b"PGDMP")

        errors = _send_to_destinations(str(dump), "")

        assert errors == []
        written = [n for n in os.listdir(tmp_path) if n != "src.dump"]
        assert len(written) == 1
        assert written[0].startswith("ohio-voice-backup-")
        assert parse_backup_dt(written[0], {"ohio-voice"}) is not None


def test_sftp_upload_uses_given_filename_and_prefixes():
    from app.services import backup_sftp

    client = MagicMock()
    sftp = client.open_sftp.return_value
    sftp.listdir.return_value = []
    prefixes = BackupNaming.for_org("Ohio Voice").prefixes
    with patch.object(backup_sftp, "make_ssh_client", return_value=client), \
         patch.object(backup_sftp, "apply_sftp_retention") as retention:
        backup_sftp.sftp_upload(
            "/tmp/x.dump", {"remote_path": "/backups/"}, schedule="daily",
            filename="ohio-voice-backup-20261112-020000.dump", prefixes=prefixes,
        )
    sftp.put.assert_called_once_with("/tmp/x.dump", "/backups/ohio-voice-backup-20261112-020000.dump")
    retention.assert_called_once_with(sftp, "/backups", "daily", prefixes)
    client.close.assert_called_once()


class TestPrefixHistory:
    def test_record_and_load(self, app):
        from app.services.backup_retention import (
            load_prefix_history,
            record_prefix_used,
        )
        assert load_prefix_history() == set()
        record_prefix_used("ohio-voice")
        record_prefix_used("ohio-voice")  # idempotent
        record_prefix_used("new-name")
        assert load_prefix_history() == {"ohio-voice", "new-name"}

    def test_invalid_entries_never_widen_retention(self, app):
        from app.models import Settings
        from app.services.backup_retention import (
            PREFIX_HISTORY_KEY,
            load_prefix_history,
            record_prefix_used,
        )
        Settings.set(PREFIX_HISTORY_KEY, "good-name\n../etc\nBAD NAME\n\n-dash-\n")
        record_prefix_used("../evil")
        assert load_prefix_history() == {"good-name"}

    def test_backup_run_records_prefix(self, app):
        from app.models import Settings
        from app.services.backup import _send_to_destinations
        from app.services.backup_retention import load_prefix_history

        Settings.set("branding_org_name", "Ohio Voice")
        with patch("app.services.backup.is_remote_configured", return_value=False), \
             patch("app.services.backup.is_local_configured", return_value=False), \
             patch("app.services.backup.is_gdrive_configured", return_value=False):
            _send_to_destinations("/tmp/fake.dump", "")
        assert "ohio-voice" in load_prefix_history()

    def test_backups_from_previous_org_name_still_age_out(self, app, tmp_path):
        from app.models import Settings
        from app.services.backup import _send_to_destinations

        Settings.set("backup_enable_remote", "false")
        Settings.set("backup_enable_local", "true")
        Settings.set("backup_local_path", str(tmp_path))
        dump = tmp_path.parent / f"{tmp_path.name}-src.dump"
        dump.write_bytes(b"PGDMP")

        # Run once under the old name so its prefix is recorded...
        Settings.set("branding_org_name", "Old Name")
        _send_to_destinations(str(dump), "")
        old_files = [f"old-name-backup-2026010{d}-020000.dump" for d in range(1, 6)]
        for n in old_files:
            (tmp_path / n).touch()
        (tmp_path / "other-app-backup-20250101-020000.dump").touch()

        # ...then rename the org and run with weekly retention.
        Settings.set("branding_org_name", "New Name")
        _send_to_destinations(str(dump), "weekly")

        remaining = set(os.listdir(tmp_path))
        new_files = [n for n in remaining if n.startswith("new-name-backup-")]
        old_today = [n for n in remaining if n.startswith("old-name-backup-") and n not in old_files]
        assert len(new_files) == 1 and len(old_today) == 1
        # Weekly keeps the 4 newest across both names: new run, old-name run,
        # then the two newest fixtures (05, 04). 01-03 are pruned.
        assert set(old_files) & remaining == {old_files[4], old_files[3]}
        assert "other-app-backup-20250101-020000.dump" in remaining


class TestDownloadAndConfigIO:
    def _admin(self, client):
        from app import db
        from tests.conftest import login, make_user
        u = make_user(role="admin")
        db.session.commit()
        login(client, u)

    def test_download_named_after_org(self, client, app, tmp_path):
        from app.models import Settings
        Settings.set("branding_org_name", "Ohio Voice")
        self._admin(client)
        dump = tmp_path / "d.dump"
        dump.write_bytes(b"PGDMP")
        with patch("app.services.backup.create_local_dump", return_value=str(dump)):
            resp = client.get("/settings/download-backup")
        assert resp.status_code == 200
        disposition = resp.headers["Content-Disposition"]
        assert "ohio-voice-backup-" in disposition
        assert "mandate-backup" not in disposition
        assert resp.data == b"PGDMP"

    def test_download_without_org_uses_legacy_name(self, client, app, tmp_path):
        self._admin(client)
        dump = tmp_path / "d.dump"
        dump.write_bytes(b"PGDMP")
        with patch("app.services.backup.create_local_dump", return_value=str(dump)):
            resp = client.get("/settings/download-backup")
        assert "petition-qc-backup-" in resp.headers["Content-Disposition"]

    def test_prefix_history_not_exported_or_imported(self, client, app):
        import io
        import json

        from app.models import Settings
        from app.services.backup_retention import PREFIX_HISTORY_KEY
        Settings.set(PREFIX_HISTORY_KEY, "ohio-voice")
        self._admin(client)
        exported = client.get("/settings/export-config").get_json()["mandate_settings"]
        assert PREFIX_HISTORY_KEY not in exported
        payload = json.dumps({"mandate_settings": {PREFIX_HISTORY_KEY: "other-campaign"}}).encode()
        client.post("/settings/import-config", data={"config_file": (io.BytesIO(payload), "c.json")},
                    content_type="multipart/form-data")
        assert Settings.get(PREFIX_HISTORY_KEY) == "ohio-voice"

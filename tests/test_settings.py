"""Tests for settings export/import: key exclusions, round-trip, access control."""
import io
import json

from conftest import make_user, login

from app import db
from app.models import Settings


# ---------------------------------------------------------------------------
# Export config
# ---------------------------------------------------------------------------

def test_export_config_excludes_sensitive_keys(client, app):
    admin = make_user(role="admin", email="admin@test.example")
    db.session.commit()
    login(client, admin)

    # Seed a sensitive key so there's something to exclude
    Settings.set("backup_scp_key_content", "super-secret-key")
    Settings.set("smtp_password", "smtp-secret")
    Settings.set("branding_logo_content", "base64encodedlogo")
    db.session.commit()

    resp = client.get("/settings/export-config")
    assert resp.status_code == 200
    payload = json.loads(resp.data)
    data = payload["mandate_settings"]

    assert "backup_scp_key_content" not in data
    assert "smtp_password" not in data
    assert "branding_logo_content" not in data


def test_export_config_includes_non_sensitive_keys(client, app):
    admin = make_user(role="admin", email="admin2@test.example")
    db.session.commit()
    login(client, admin)

    Settings.set("target_city", "EXPORT TEST CITY")
    Settings.set("signature_goal", "9999")
    db.session.commit()

    resp = client.get("/settings/export-config")
    assert resp.status_code == 200
    payload = json.loads(resp.data)
    data = payload["mandate_settings"]

    assert data.get("target_city") == "EXPORT TEST CITY"
    assert data.get("signature_goal") == "9999"


def test_export_config_has_exported_at_field(client, app):
    admin = make_user(role="admin", email="admin3@test.example")
    db.session.commit()
    login(client, admin)

    resp = client.get("/settings/export-config")
    payload = json.loads(resp.data)
    assert "exported_at" in payload


def test_export_requires_admin(client, app):
    """Organizer should be redirected, not able to export."""
    organizer = make_user(role="organizer", email="org@test.example")
    db.session.commit()
    login(client, organizer)

    resp = client.get("/settings/export-config", follow_redirects=False)
    assert resp.status_code == 302
    # Redirects to main.index, not login
    assert "/auth/login" not in resp.location


def test_export_requires_auth(client):
    resp = client.get("/settings/export-config", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.location


# ---------------------------------------------------------------------------
# Import config
# ---------------------------------------------------------------------------

def test_import_config_restores_known_keys(client, app):
    admin = make_user(role="admin", email="admin4@test.example")
    db.session.commit()
    login(client, admin)

    payload = json.dumps({
        "mandate_settings": {
            "target_city": "IMPORT TEST CITY",
            "signature_goal": "1234",
        },
        "exported_at": "2026-01-01T00:00:00",
    })

    resp = client.post(
        "/settings/import-config",
        data={"config_file": (io.BytesIO(payload.encode()), "config.json")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db.session.expire_all()
    assert Settings.get("target_city") == "IMPORT TEST CITY"
    assert Settings.get("signature_goal") == "1234"


def test_import_config_ignores_sensitive_keys(client, app):
    """Sensitive keys in the import file should not be written."""
    admin = make_user(role="admin", email="admin5@test.example")
    db.session.commit()
    login(client, admin)

    payload = json.dumps({
        "mandate_settings": {
            "backup_scp_key_content": "injected-key",
            "smtp_password": "injected-password",
            "branding_logo_content": "injected-logo",
            "target_city": "SAFE VALUE",
        },
        "exported_at": "2026-01-01T00:00:00",
    })

    client.post(
        "/settings/import-config",
        data={"config_file": (io.BytesIO(payload.encode()), "config.json")},
        content_type="multipart/form-data",
    )

    db.session.expire_all()
    assert Settings.get("backup_scp_key_content") is None
    assert Settings.get("smtp_password") is None
    assert Settings.get("branding_logo_content") is None
    assert Settings.get("target_city") == "SAFE VALUE"


def test_import_config_ignores_unknown_keys(client, app):
    """Extra keys not in the DB schema should be silently skipped."""
    admin = make_user(role="admin", email="admin6@test.example")
    db.session.commit()
    login(client, admin)

    payload = json.dumps({
        "mandate_settings": {
            "some_future_key_that_doesnt_exist": "value",
            "target_city": "KNOWN CITY",
        },
        "exported_at": "2026-01-01T00:00:00",
    })

    resp = client.post(
        "/settings/import-config",
        data={"config_file": (io.BytesIO(payload.encode()), "config.json")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    # Should succeed without crashing
    assert resp.status_code == 200
    db.session.expire_all()
    assert Settings.get("target_city") == "KNOWN CITY"


def test_import_config_round_trip(client, app):
    """Export → import should restore all non-sensitive settings."""
    admin = make_user(role="admin", email="admin7@test.example")
    db.session.commit()
    login(client, admin)

    # Set some values
    Settings.set("target_city", "ROUNDTRIP CITY")
    Settings.set("signature_goal", "7777")
    db.session.commit()

    # Export
    export_resp = client.get("/settings/export-config")
    exported_data = export_resp.data

    # Wipe the settings
    Settings.set("target_city", "CLEARED")
    Settings.set("signature_goal", "0")
    db.session.commit()

    # Re-import
    client.post(
        "/settings/import-config",
        data={"config_file": (io.BytesIO(exported_data), "config.json")},
        content_type="multipart/form-data",
    )

    db.session.expire_all()
    assert Settings.get("target_city") == "ROUNDTRIP CITY"
    assert Settings.get("signature_goal") == "7777"


def test_import_requires_admin(client, app):
    organizer = make_user(role="organizer", email="org2@test.example")
    db.session.commit()
    login(client, organizer)

    payload = json.dumps({"mandate_settings": {}, "exported_at": "2026-01-01"})
    resp = client.post(
        "/settings/import-config",
        data={"config_file": (io.BytesIO(payload.encode()), "config.json")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302

"""Tests for backup email functions — subject, footer, backward compatibility."""
from unittest.mock import patch


def _call_success(email_ctx=None):
    from app.services.email import send_backup_success_email
    with patch("app.services.email.send_email") as mock_send:
        send_backup_success_email("to@example.com", "2026-05-08T03:00:00", email_ctx)
        return mock_send.call_args  # (to, subject, body_html, body_text)


def _call_failure(email_ctx=None):
    from app.services.email import send_backup_failure_email
    with patch("app.services.email.send_email") as mock_send:
        send_backup_failure_email("to@example.com", "disk full", "2026-05-08T03:00:00", email_ctx)
        return mock_send.call_args


def _call_digest(email_ctx=None):
    from app.services.email import send_backup_digest_email
    with patch("app.services.email.send_email") as mock_send:
        send_backup_digest_email("to@example.com", ["2026-05-08T01:00:00", "2026-05-08T02:00:00"], email_ctx)
        return mock_send.call_args


# ---------------------------------------------------------------------------
# Backward compatibility — no context
# ---------------------------------------------------------------------------

def test_success_no_context_subject():
    _, subject, _, _ = _call_success()[0]
    assert subject == "Backup Succeeded"


def test_failure_no_context_subject():
    _, subject, _, _ = _call_failure()[0]
    assert subject == "Backup Failed"


def test_digest_no_context_subject():
    _, subject, _, _ = _call_digest()[0]
    assert subject == "Backup Digest — 2 backups"


def test_success_no_context_no_footer():
    _, _, body_html, body_text = _call_success()[0]
    assert "View Mandate" not in body_html
    assert "View app" not in body_text


# ---------------------------------------------------------------------------
# org_name prefix in subject
# ---------------------------------------------------------------------------

def test_success_org_name_in_subject():
    ctx = {"org_name": "Test Campaign", "site_url": ""}
    _, subject, _, _ = _call_success(ctx)[0]
    assert subject == "Test Campaign — Backup Succeeded"


def test_failure_org_name_in_subject():
    ctx = {"org_name": "Test Campaign", "site_url": ""}
    _, subject, _, _ = _call_failure(ctx)[0]
    assert subject == "Test Campaign — Backup Failed"


def test_digest_org_name_in_subject():
    ctx = {"org_name": "Test Campaign", "site_url": ""}
    _, subject, _, _ = _call_digest(ctx)[0]
    assert subject == "Test Campaign — Backup Digest — 2 backups"


def test_empty_org_name_no_prefix():
    ctx = {"org_name": "", "site_url": ""}
    _, subject, _, _ = _call_success(ctx)[0]
    assert subject == "Backup Succeeded"


# ---------------------------------------------------------------------------
# site_url footer link
# ---------------------------------------------------------------------------

def test_success_site_url_in_html():
    ctx = {"org_name": "", "site_url": "https://petition.example.com"}
    _, _, body_html, _ = _call_success(ctx)[0]
    assert "https://petition.example.com" in body_html
    assert "View Mandate" in body_html


def test_success_site_url_in_plain_text():
    ctx = {"org_name": "", "site_url": "https://petition.example.com"}
    _, _, _, body_text = _call_success(ctx)[0]
    assert "https://petition.example.com" in body_text


def test_failure_site_url_in_html():
    ctx = {"org_name": "", "site_url": "https://petition.example.com"}
    _, _, body_html, _ = _call_failure(ctx)[0]
    assert "https://petition.example.com" in body_html


def test_digest_site_url_in_html():
    ctx = {"org_name": "", "site_url": "https://petition.example.com"}
    _, _, body_html, _ = _call_digest(ctx)[0]
    assert "https://petition.example.com" in body_html


def test_empty_site_url_no_footer():
    ctx = {"org_name": "Test Campaign", "site_url": ""}
    _, _, body_html, body_text = _call_success(ctx)[0]
    assert "View Mandate" not in body_html
    assert "View app" not in body_text

# Backup Email Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add org name (subject prefix) and app hostname (footer link) to backup success, failure, and digest emails via a shared `Settings.get_email_context()` helper.

**Architecture:** New `Settings.get_email_context()` classmethod resolves `site_url` (DB → `CAMPAIGN1_DOMAIN` env var → empty) and `org_name` (existing `branding_org_name`). All three backup email functions gain an `email_ctx: dict | None = None` parameter. Callers in `backup.py` and `scheduler.py` fetch the context once and pass it through. A new Site URL field in the admin settings form saves the `site_url` DB key.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy, Jinja2, pytest

---

## File Map

| File | Change |
|---|---|
| `app/models/settings.py` | Add `get_email_context()` classmethod |
| `app/services/email.py` | Add `email_ctx` param to 3 backup email functions |
| `app/services/backup.py` | Fetch and pass `email_ctx` in `_send_backup_notification()` |
| `app/services/scheduler.py` | Fetch and pass `email_ctx` in `_run_digest()` |
| `app/routes/settings.py` | Save `site_url` from POST; pass `site_url` to template |
| `app/templates/settings/index.html` | Add Site URL input to Notifications subsection |
| `tests/test_settings.py` | Tests for `get_email_context()` |
| `tests/test_email.py` | New file — tests for updated backup email functions |

---

### Task 1: `Settings.get_email_context()` — tests first

**Files:**
- Modify: `tests/test_settings.py`
- Modify: `app/models/settings.py` (after `get_backup_notify_config`, before `save_backup_config`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings.py`:

```python
# ---------------------------------------------------------------------------
# get_email_context
# ---------------------------------------------------------------------------

def test_get_email_context_returns_db_site_url(app):
    from app.models import Settings
    Settings.set("site_url", "https://petition.example.com")
    Settings.set("branding_org_name", "Test Campaign")
    ctx = Settings.get_email_context()
    assert ctx["site_url"] == "https://petition.example.com"
    assert ctx["org_name"] == "Test Campaign"


def test_get_email_context_env_fallback(app, monkeypatch):
    from app.models import Settings
    Settings.set("site_url", "")
    monkeypatch.setenv("CAMPAIGN1_DOMAIN", "petition.example.com")
    ctx = Settings.get_email_context()
    assert ctx["site_url"] == "https://petition.example.com"


def test_get_email_context_db_overrides_env(app, monkeypatch):
    from app.models import Settings
    Settings.set("site_url", "https://override.example.com")
    monkeypatch.setenv("CAMPAIGN1_DOMAIN", "env.example.com")
    ctx = Settings.get_email_context()
    assert ctx["site_url"] == "https://override.example.com"


def test_get_email_context_empty_when_nothing_configured(app, monkeypatch):
    from app.models import Settings
    Settings.set("site_url", "")
    monkeypatch.delenv("CAMPAIGN1_DOMAIN", raising=False)
    ctx = Settings.get_email_context()
    assert ctx["site_url"] == ""
    assert ctx["org_name"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/adam/Projects/Mandate && python -m pytest tests/test_settings.py::test_get_email_context_returns_db_site_url -v
```

Expected: `FAILED` with `AttributeError: type object 'Settings' has no attribute 'get_email_context'`

- [ ] **Step 3: Implement `get_email_context()`**

In `app/models/settings.py`, add after the `save_backup_notify_config` method (around line 196) and before `get_digest_pending`:

```python
    @classmethod
    def get_email_context(cls) -> dict:
        """Return shared context dict for backup notification emails.

        site_url resolution order:
          1. ``site_url`` DB key (set via admin UI)
          2. ``https://{CAMPAIGN1_DOMAIN}`` env var (Docker deployments)
          3. ``""`` — caller renders no link
        """
        import os
        site_url = cls.get("site_url", "").strip()
        if not site_url:
            domain = os.environ.get("CAMPAIGN1_DOMAIN", "").strip()
            if domain:
                site_url = f"https://{domain}"
        return {
            "site_url": site_url,
            "org_name": cls.get("branding_org_name", "").strip(),
        }
```

- [ ] **Step 4: Run all four tests**

```bash
cd /home/adam/Projects/Mandate && python -m pytest tests/test_settings.py -k "get_email_context" -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add app/models/settings.py tests/test_settings.py
git commit -m "feat: add Settings.get_email_context() with site_url + org_name"
```

---

### Task 2: Update backup email functions

**Files:**
- Create: `tests/test_email.py`
- Modify: `app/services/email.py`

- [ ] **Step 1: Create `tests/test_email.py` with failing tests**

```python
"""Tests for backup email functions — subject, footer, backward compatibility."""
from unittest.mock import patch

import pytest


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
    args = _call_success()
    _, subject, _, _ = args[0]
    assert subject == "Backup Succeeded"


def test_failure_no_context_subject():
    args = _call_failure()
    _, subject, _, _ = args[0]
    assert subject == "Backup Failed"


def test_digest_no_context_subject():
    args = _call_digest()
    _, subject, _, _ = args[0]
    assert subject == "Backup Digest — 2 backups"


def test_success_no_context_no_footer():
    args = _call_success()
    _, _, body_html, body_text = args[0]
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/adam/Projects/Mandate && python -m pytest tests/test_email.py -v 2>&1 | head -40
```

Expected: multiple FAILED — `send_backup_success_email() takes 2 positional arguments but 3 were given` (or similar)

- [ ] **Step 3: Update `app/services/email.py` — replace the three backup email functions**

Replace `send_backup_success_email` (lines 49–55):

```python
def send_backup_success_email(to: str, backup_time_iso: str, email_ctx: dict | None = None) -> None:
    """Send a single-backup success notification."""
    ctx = email_ctx or {}
    org_name = ctx.get("org_name", "")
    site_url = ctx.get("site_url", "")
    prefix = f"{org_name} — " if org_name else ""
    subject = f"{prefix}Backup Succeeded"
    backup_time = backup_time_iso[:19].replace("T", " ") if backup_time_iso else "unknown"
    footer_text = f"\nView app: {site_url}" if site_url else ""
    footer_html = (
        f'<p style="margin-top:20px;"><a href="{site_url}" style="color:#0c3e6b;">View Mandate →</a></p>'
        if site_url else ""
    )
    body_text = f"A database backup completed successfully at {backup_time} UTC.{footer_text}"
    body_html = (
        f"<p>A database backup completed successfully at <strong>{backup_time} UTC</strong>.</p>"
        f"{footer_html}"
    )
    send_email(to, subject, body_html, body_text)
```

Replace `send_backup_failure_email` (lines 58–71):

```python
def send_backup_failure_email(to: str, error_msg: str, backup_time_iso: str, email_ctx: dict | None = None) -> None:
    """Send an immediate failure alert with error detail."""
    ctx = email_ctx or {}
    org_name = ctx.get("org_name", "")
    site_url = ctx.get("site_url", "")
    prefix = f"{org_name} — " if org_name else ""
    subject = f"{prefix}Backup Failed"
    backup_time = backup_time_iso[:19].replace("T", " ") if backup_time_iso else "unknown"
    footer_text = f"\nView app: {site_url}" if site_url else ""
    footer_html = (
        f'<p style="margin-top:20px;"><a href="{site_url}" style="color:#0c3e6b;">View Mandate →</a></p>'
        if site_url else ""
    )
    body_text = (
        f"A database backup failed at {backup_time} UTC.\n\n"
        f"Error: {error_msg}{footer_text}"
    )
    body_html = (
        f"<p>A database backup failed at <strong>{backup_time} UTC</strong>.</p>"
        f"<p><strong>Error:</strong></p>"
        f"<pre style='background:#f5f5f5;padding:8px;border-radius:4px'>{error_msg}</pre>"
        f"{footer_html}"
    )
    send_email(to, subject, body_html, body_text)
```

Replace `send_backup_digest_email` (lines 74–91):

```python
def send_backup_digest_email(to: str, entries: list, email_ctx: dict | None = None) -> None:
    """Send a digest listing all successful backup timestamps."""
    ctx = email_ctx or {}
    org_name = ctx.get("org_name", "")
    site_url = ctx.get("site_url", "")
    prefix = f"{org_name} — " if org_name else ""
    count = len(entries)
    subject = f"{prefix}Backup Digest — {count} backup{'s' if count != 1 else ''}"
    formatted = "\n".join(
        f"  • {ts[:19].replace('T', ' ')} UTC" for ts in entries
    )
    footer_text = f"\nView app: {site_url}" if site_url else ""
    footer_html = (
        f'<p style="margin-top:20px;"><a href="{site_url}" style="color:#0c3e6b;">View Mandate →</a></p>'
        if site_url else ""
    )
    body_text = (
        f"{count} backup{'s' if count != 1 else ''} completed successfully:\n\n"
        f"{formatted}{footer_text}"
    )
    items_html = "".join(
        f"<li>{ts[:19].replace('T', ' ')} UTC</li>" for ts in entries
    )
    body_html = (
        f"<p>{count} backup{'s' if count != 1 else ''} completed successfully:</p>"
        f"<ul>{items_html}</ul>"
        f"{footer_html}"
    )
    send_email(to, subject, body_html, body_text)
```

- [ ] **Step 4: Run all email tests**

```bash
cd /home/adam/Projects/Mandate && python -m pytest tests/test_email.py -v
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add app/services/email.py tests/test_email.py
git commit -m "feat: add email_ctx param to backup email functions (org name + site url)"
```

---

### Task 3: Wire up callers in backup.py and scheduler.py

**Files:**
- Modify: `app/services/backup.py` — `_send_backup_notification()` (lines 151–169)
- Modify: `app/services/scheduler.py` — `_run_digest()` (lines 106–141)

No new tests needed — the email functions are already tested in Task 2; the callers are integration paths exercised by the email tests above.

- [ ] **Step 1: Update `_send_backup_notification()` in `app/services/backup.py`**

Replace the function body (keeping the signature identical):

```python
def _send_backup_notification(success: bool, error_msg: str | None) -> None:
    """Send an email notification for a backup success or failure."""
    from app.models import Settings
    from app.services import email as email_service

    notify_email = Settings.get("backup_notify_email", "").strip()
    if not notify_email or not email_service.is_configured():
        return

    email_ctx = Settings.get_email_context()
    backup_time = Settings.get("backup_last_run", "")
    if success:
        mode = Settings.get("backup_notify_success", "")
        if mode == "each":
            email_service.send_backup_success_email(notify_email, backup_time, email_ctx)
        elif mode in ("daily", "weekly"):
            Settings.add_digest_pending(backup_time)
    else:
        if Settings.get("backup_notify_failure", "false") == "true":
            email_service.send_backup_failure_email(notify_email, error_msg or "", backup_time, email_ctx)
```

- [ ] **Step 2: Update `_run_digest()` in `app/services/scheduler.py`**

Replace the function body (keeping the signature `def _run_digest(app, frequency: str) -> None:`):

```python
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

        email_ctx = Settings.get_email_context()
        try:
            email_service.send_backup_digest_email(notify_email, entries, email_ctx)
            Settings.clear_digest_pending()
        except Exception:
            logger.exception("Digest email failed (%s)", frequency)
```

- [ ] **Step 3: Run the full test suite to confirm nothing is broken**

```bash
cd /home/adam/Projects/Mandate && python -m pytest tests/ -v 2>&1 | tail -20
```

Expected: all PASSED (same count as before plus the new tests from Tasks 1–2)

- [ ] **Step 4: Commit**

```bash
git add app/services/backup.py app/services/scheduler.py
git commit -m "feat: pass email_ctx through backup notification callers"
```

---

### Task 4: Admin UI — Site URL field

**Files:**
- Modify: `app/routes/settings.py` — `index()` view + `save_backup_config()` route
- Modify: `app/templates/settings/index.html` — Notifications subsection

- [ ] **Step 1: Update `save_backup_config()` in `app/routes/settings.py`**

In `save_backup_config()` (around line 130, after `Settings.set("backup_schedule", schedule)`), add:

```python
    Settings.set("site_url", request.form.get("site_url", "").strip())
```

- [ ] **Step 2: Pass `site_url` to the settings template from `index()`**

In the `index()` view (around line 76, after `notify_config = Settings.get_backup_notify_config()`), add:

```python
    site_url = Settings.get("site_url", "")
```

Then add `site_url=site_url` to the `render_template(...)` call (around line 84).

- [ ] **Step 3: Add Site URL input to the template**

In `app/templates/settings/index.html`, inside the `<!-- Notifications subsection -->` div (around line 461, immediately before the Notification Email `<div>`), insert:

```html
                <div>
                    <label for="site_url" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        Site URL
                    </label>
                    <p class="text-xs text-gray-500 dark:text-gray-400 mb-1">
                        Included as a link in backup notification emails.
                    </p>
                    <input type="url" name="site_url" id="site_url"
                           value="{{ site_url }}"
                           placeholder="https://petition.example.com"
                           class="block w-full rounded-md bg-gray-50 dark:bg-gray-700 border-gray-300 dark:border-gray-600 text-gray-900 dark:text-white px-3 py-2 text-sm focus:border-navy-500 focus:ring-navy-500">
                </div>
```

- [ ] **Step 4: Run the test suite**

```bash
cd /home/adam/Projects/Mandate && python -m pytest tests/ -v 2>&1 | tail -20
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add app/routes/settings.py app/templates/settings/index.html
git commit -m "feat: add Site URL field to backup settings for email context"
```

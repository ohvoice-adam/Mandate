# Design: Backup Email Context — Hostname Link + Org Name

**Date:** 2026-05-08  
**Status:** Approved

## Problem

Backup notification emails (success, failure, digest) contain no link back to the Mandate app and no reference to which campaign instance sent them. Recipients must manually navigate to the app, and when managing multiple campaigns the emails are indistinguishable.

## Solution

Introduce a `Settings.get_email_context()` helper that returns a shared context dict (`site_url`, `org_name`). All three backup email functions accept this context and use it to:

- Prefix the subject with the org name (when set)
- Render a "View Mandate →" link at the bottom of each email (when `site_url` is set)

## Design

### 1. `Settings.get_email_context()` — new classmethod

Location: `app/models/settings.py`

```python
@classmethod
def get_email_context(cls) -> dict:
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

**`site_url` resolution order:**
1. `site_url` DB key (set via admin UI)
2. `https://{CAMPAIGN1_DOMAIN}` env var (Docker deployments)
3. `""` — no link rendered

**`org_name`:** reads existing `branding_org_name` key. No new DB key needed.

### 2. Email function changes

Location: `app/services/email.py`

All three backup email functions gain `email_ctx: dict | None = None`:

```python
def send_backup_success_email(to: str, backup_time_iso: str, email_ctx: dict | None = None) -> None: ...
def send_backup_failure_email(to: str, error_msg: str, backup_time_iso: str, email_ctx: dict | None = None) -> None: ...
def send_backup_digest_email(to: str, entries: list, email_ctx: dict | None = None) -> None: ...
```

**Subject line:** `"{org_name} — Backup Succeeded"` when org name is set; `"Backup Succeeded"` when not.

**HTML footer (when `site_url` is set):**
```html
<p style="margin-top:20px;">
  <a href="{site_url}" style="color:#0c3e6b;">View Mandate →</a>
</p>
```

**Plain text footer (when `site_url` is set):**
```
View app: {site_url}
```

When `email_ctx` is `None` or fields are empty, emails render exactly as today — no broken links, no empty headers.

### 3. Caller update

Location: `app/services/backup.py` → `_send_backup_notification()`

Fetch context once and pass to each email call:

```python
email_ctx = Settings.get_email_context()
email_service.send_backup_success_email(notify_email, backup_time, email_ctx)
# same for failure; digest via scheduler reads context in _run_digest()
```

The scheduler's `_run_digest()` in `app/services/scheduler.py` also needs updating to fetch and pass `email_ctx` when calling `send_backup_digest_email`.

### 4. Admin UI — Site URL field

Location: existing backup settings page/template

- New text input bound to `site_url` DB key
- Placeholder: `https://petition.example.com`
- Saved via the existing backup settings POST handler (no new route needed)
- Positioned alongside the existing SCP host/path fields

## Files Changed

| File | Change |
|---|---|
| `app/models/settings.py` | Add `get_email_context()` classmethod |
| `app/services/email.py` | Add `email_ctx` param to 3 backup email functions; update subjects and bodies |
| `app/services/backup.py` | Fetch `email_ctx` in `_send_backup_notification()` and pass through |
| `app/services/scheduler.py` | Fetch `email_ctx` in `_run_digest()` and pass through |
| `app/routes/settings.py` | Save `site_url` from form POST |
| `app/templates/settings/...` | Add Site URL input field to backup settings form |

## Out of Scope

- Invitation and password reset emails (already have request context and `url_for`)
- Digest emails sent from non-backup contexts (none exist currently)
- HTML email redesign / templating refactor

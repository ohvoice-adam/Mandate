# Backup Destination Selection — Design Spec

**Date:** 2026-05-22  
**Status:** Approved

## Overview

Add the ability to send scheduled and manual backups to a remote SFTP server, a local filesystem path, or both — independently enabled and configured. Both destinations share the same schedule and the same retention policy.

## New Settings (DB keys)

| Key | Values | Default | Notes |
|-----|--------|---------|-------|
| `backup_enable_remote` | `"true"` / `"false"` | `"true"` | Preserves existing behavior for installs that already have SCP configured |
| `backup_enable_local` | `"true"` / `"false"` | `"false"` | |
| `backup_local_path` | filesystem path string | `""` | e.g. `/var/backups/mandate` — must exist and be writable |

All existing SCP keys (`backup_scp_host`, `backup_scp_port`, `backup_scp_user`, `backup_scp_key_content`, `backup_scp_remote_path`) are unchanged.

## Configuration Helpers (`app/services/backup.py`)

Replace `is_configured()` with three functions:

- `is_remote_configured()` — returns `True` if `backup_enable_remote == "true"` AND all four SCP settings are present
- `is_local_configured()` — returns `True` if `backup_enable_local == "true"` AND `backup_local_path` is non-empty
- `is_configured()` — returns `True` if either of the above passes; used for "Run Backup" button visibility and schedule eligibility

Each destination operates independently: a misconfigured remote does not prevent local from running, and vice versa.

## Backup Execution (`_backup_thread`)

1. Create the pg_dump temp file once (unchanged)
2. If `is_remote_configured()`: attempt `_sftp_upload()`, capture result
3. If `is_local_configured()`: attempt `_local_save()`, capture result
4. Both destinations always run even if the other fails — errors are collected
5. Final status: `"success"` if all attempted destinations succeeded; `"error: <combined message>"` if any failed
6. Temp file deleted in `finally` block regardless of outcome (unchanged)

## New Functions (`app/services/backup.py`)

### `_local_save(dump_path, local_dir, schedule)`
- Copies the temp dump to `{local_dir}/petition-qc-backup-{timestamp}.dump`
- Calls `_apply_local_retention(local_dir, schedule)` after copy
- Raises `RuntimeError` on failure (same pattern as `_sftp_upload`)

### `_apply_local_retention(local_dir, schedule)`
- Same retention logic as `_apply_retention()` (hourly/daily/weekly rules, same keep counts)
- Uses `os.listdir()` to enumerate files and `os.remove()` to delete old ones
- Silently skips files it cannot remove (same behavior as the SFTP version)

## Settings Model (`app/models/settings.py`)

- `get_backup_config()` gains three new keys: `enable_remote`, `enable_local`, `local_path`
- `save_backup_config()` gains `enable_remote`, `enable_local`, `local_path` parameters

## UI (`app/templates/settings/index.html`)

Two new checkboxes near the top of the backup config form:

- **"Upload via SFTP"** (`backup_enable_remote`) — checked by default on first load if SCP is already configured
- **"Save to local directory"** (`backup_enable_local`)

Behavior:
- The SCP credential fields (host, port, user, key, remote path) are shown/hidden via CSS `display:none` — NOT removed from the DOM — so their values are always submitted with the form and stored SCP credentials are never erased simply by unchecking the checkbox
- A new **"Local backup path"** text input follows the same rule: hidden via CSS when the local checkbox is unchecked, but still submitted
- Both checkboxes can be checked simultaneously
- JS validation on form submit: if a schedule is selected but neither checkbox is checked, block submission with an inline error

## `run_backup_async()` (`app/services/backup.py`)

Updated `is_configured()` call (now checks either destination). No other changes.

## `run_backup_sync()` (`app/services/backup.py`)

Same — just relies on the updated `is_configured()` and `_backup_thread()`.

## Backward Compatibility

Existing installs with SCP credentials already saved will continue to work without any migration. `backup_enable_remote` defaults to `"true"` (via `Settings.get("backup_enable_remote", "true")`), so the existing remote-only behavior is preserved on upgrade.

## Out of Scope

- Per-destination schedules (both always use `backup_schedule`)
- Per-destination notification settings (both use the existing notify config)
- Local backup encryption

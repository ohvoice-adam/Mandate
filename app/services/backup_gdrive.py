"""Google Drive backup destination (service account + Shared Drive).

Uploads pg_dump files to a folder on a Google Shared Drive using a service
account key, and applies the same retention policy as the SFTP and local
destinations.  Service accounts have no storage quota of their own, so the
target folder MUST live on a Shared Drive (Google Workspace).

Talks to the Drive REST v3 API directly via ``google-auth``'s
``AuthorizedSession``; the heavier ``google-api-python-client`` is not needed.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any

import requests
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from app.services.backup_retention import select_backups_to_keep, sort_backup_names

logger = logging.getLogger(__name__)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
_API = "https://www.googleapis.com/drive/v3"
_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_TIMEOUT = 60
_UPLOAD_TIMEOUT = 600

_FOLDER_URL_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")
_FOLDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class DriveError(RuntimeError):
    """A Drive API call failed; message is safe to show to admins."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# Errors retention can log and move past without failing the backup itself.
_RETENTION_ERRORS = (DriveError, requests.RequestException, GoogleAuthError)


def parse_service_account(json_text: str) -> dict[str, Any]:
    """Validate a service-account key JSON string and return it as a dict.

    Raises ValueError with an admin-friendly message when the file is not a
    usable service-account key.
    """
    try:
        info = json.loads(json_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Key file is not valid JSON.") from exc
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise ValueError(
            "Key file is not a service-account key (expected \"type\": \"service_account\")."
        )
    missing = [k for k in ("client_email", "private_key") if not info.get(k)]
    if missing:
        raise ValueError(f"Service-account key is missing: {', '.join(missing)}.")
    return info


def client_email(json_text: str | None) -> str:
    """Return the service account's email from stored key JSON, or ''."""
    if not json_text:
        return ""
    try:
        return str(parse_service_account(json_text)["client_email"])
    except ValueError:
        return ""


def normalize_folder_id(value: str) -> str:
    """Accept a bare folder ID or a Drive folder URL; return the ID or ''."""
    value = (value or "").strip()
    if not value:
        return ""
    match = _FOLDER_URL_RE.search(value)
    if match:
        return match.group(1)
    return value if _FOLDER_ID_RE.match(value) else ""


def _session(sa_json: str) -> AuthorizedSession:
    """Return an authorised HTTP session for the service account."""
    info = parse_service_account(sa_json)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=[DRIVE_SCOPE]
    )
    return AuthorizedSession(creds)


def _raise_for_status(resp: Any, action: str) -> None:
    """Raise DriveError with Google's error message if *resp* failed."""
    if resp.status_code < 400:
        return
    try:
        detail = resp.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    detail = (detail or resp.text or "").strip()[:200]
    raise DriveError(f"{action} failed (HTTP {resp.status_code}): {detail}", resp.status_code)


# ---------------------------------------------------------------------------
# Upload + retention
# ---------------------------------------------------------------------------

def upload(dump_path: str, sa_json: str, folder_id: str, schedule: str = "") -> str:
    """Upload *dump_path* to the Drive folder, apply retention, return file ID."""
    session = _session(sa_json)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"petition-qc-backup-{timestamp}.dump"

    init = session.post(
        f"{_UPLOAD_API}/files",
        params={"uploadType": "resumable", "supportsAllDrives": "true"},
        json={"name": name, "parents": [folder_id]},
        headers={
            "X-Upload-Content-Type": "application/octet-stream",
            "X-Upload-Content-Length": str(os.path.getsize(dump_path)),
        },
        timeout=_TIMEOUT,
    )
    _raise_for_status(init, "Starting Drive upload")
    upload_url = init.headers.get("Location")
    if not upload_url:
        raise DriveError("Drive did not return an upload session URL.")

    with open(dump_path, "rb") as fh:
        resp = session.put(
            upload_url,
            data=fh,
            headers={"Content-Type": "application/octet-stream"},
            timeout=_UPLOAD_TIMEOUT,
        )
    _raise_for_status(resp, "Uploading backup to Drive")
    file_id = str(resp.json().get("id", ""))
    logger.info("Drive backup uploaded: %s (%s)", name, file_id)

    _apply_retention(session, folder_id, schedule)
    return file_id


def _list_folder(session: AuthorizedSession, folder_id: str) -> list[dict[str, str]]:
    """Return [{id, name}] for every non-trashed file in the folder."""
    files: list[dict[str, str]] = []
    params: dict[str, str] = {
        "q": f"'{folder_id}' in parents and trashed = false",
        "fields": "nextPageToken, files(id, name)",
        "pageSize": "1000",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    while True:
        resp = session.get(f"{_API}/files", params=params, timeout=_TIMEOUT)
        _raise_for_status(resp, "Listing Drive folder")
        body = resp.json()
        files.extend(body.get("files", []))
        token = body.get("nextPageToken")
        if not token:
            return files
        params["pageToken"] = token


def _apply_retention(session: AuthorizedSession, folder_id: str, schedule: str) -> None:
    """Trash Drive backups that fall outside the retention policy.

    Files are moved to the Shared Drive's trash (auto-purged after 30 days)
    rather than deleted, which only needs the Content manager role.
    """
    if schedule not in ("hourly", "daily", "weekly"):
        return
    try:
        files = _list_folder(session, folder_id)
    except _RETENTION_ERRORS as exc:
        logger.warning("Drive retention: could not list folder %s: %s", folder_id, exc)
        return

    ids_by_name = {f["name"]: f["id"] for f in files}
    backups = sort_backup_names(list(ids_by_name))
    keep = select_backups_to_keep(backups, schedule)

    for _, name in backups:
        if name in keep:
            continue
        try:
            resp = session.patch(
                f"{_API}/files/{ids_by_name[name]}",
                params={"supportsAllDrives": "true"},
                json={"trashed": True},
                timeout=_TIMEOUT,
            )
            _raise_for_status(resp, f"Trashing {name}")
            logger.info("Drive retention: trashed %s", name)
        except _RETENTION_ERRORS as exc:
            logger.warning("Drive retention: could not trash %s: %s", name, exc)


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------

def test_connection(sa_json: str, folder_id: str) -> tuple[bool, str]:
    """Check the key and folder are usable; return (ok, admin-facing message)."""
    try:
        email = str(parse_service_account(sa_json)["client_email"])
        session = _session(sa_json)
        resp = session.get(
            f"{_API}/files/{folder_id}",
            params={
                "fields": "id, name, mimeType, driveId, capabilities/canAddChildren",
                "supportsAllDrives": "true",
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 404:
            return False, (
                f"Folder not found. Check the folder ID, and share the folder "
                f"(or its Shared Drive) with {email} as Content manager."
            )
        _raise_for_status(resp, "Reading Drive folder")
        return _evaluate_folder(resp.json())
    except (ValueError, DriveError) as exc:
        return False, str(exc)
    except Exception as exc:
        logger.exception("Unexpected error testing Google Drive connection")
        return False, f"Could not reach Google Drive: {str(exc)[:200]}"


def _evaluate_folder(meta: dict[str, Any]) -> tuple[bool, str]:
    """Turn folder metadata into a (ok, message) verdict."""
    name = meta.get("name", "")
    if meta.get("mimeType") != _FOLDER_MIME:
        return False, f"\"{name}\" is not a folder."
    if not meta.get("driveId"):
        return False, (
            f"\"{name}\" is in a personal My Drive. Service accounts have no storage "
            "quota there, so the folder must be on a Shared Drive."
        )
    if not meta.get("capabilities", {}).get("canAddChildren"):
        return False, (
            f"The service account can see \"{name}\" but cannot add files. "
            "Give it the Content manager role on the Shared Drive."
        )
    return True, f"Connected. Backups will be uploaded to \"{name}\"."

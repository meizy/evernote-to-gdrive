"""
Google Drive API operations: folder management and file uploads.
"""

from __future__ import annotations

import io
import re
import time
from datetime import datetime
from typing import Any

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

# Max retries for rate-limit / transient errors
_MAX_RETRIES = 5
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _retry(fn, *args, **kwargs):
    """Call fn(*args, **kwargs) with exponential backoff on transient errors."""
    delay = 1.0
    for attempt in range(_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except HttpError as exc:
            if exc.status_code not in _RETRY_STATUS or attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(delay)
            delay *= 2
    # unreachable
    raise RuntimeError("retry loop exited unexpectedly")


# ── folder helpers ────────────────────────────────────────────────────────────

def get_or_create_folder(drive, name: str, parent_id: str | None = None) -> str:
    """Return the Drive folder ID for `name`, creating it if needed."""
    q = f"name = {_quote(name)} and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        q += f" and '{parent_id}' in parents"

    resp = _retry(
        drive.files().list(q=q, fields="files(id, name)", spaces="drive").execute
    )
    files = resp.get("files", [])
    if files:
        return files[0]["id"]

    metadata: dict[str, Any] = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    folder = _retry(
        drive.files().create(body=metadata, fields="id").execute
    )
    return folder["id"]


def get_or_create_folder_path(drive, path: str) -> str:
    """Create nested folders for a slash-separated path and return the leaf folder ID."""
    parts = [p for p in re.split(r"[/\\]", path) if p]
    parent_id: str | None = None
    for part in parts:
        parent_id = get_or_create_folder(drive, part, parent_id=parent_id)
    assert parent_id is not None
    return parent_id


def ensure_folder_path(drive, root_name: str, notebook_name: str, stack: str | None = None) -> tuple[str, str]:
    """
    Ensure root_folder/[stack/]notebook_folder exist in My Drive.
    root_name may be a slash-separated path (e.g. "a/b/c").
    Returns (root_id, notebook_id).

    Drive structure:
      root_name/notebook_name/           (no stack)
      root_name/stack/notebook_name/     (with stack)
    """
    root_id = get_or_create_folder_path(drive, root_name)
    parent_id = root_id
    if stack:
        parent_id = get_or_create_folder(drive, stack, parent_id=root_id)
    notebook_id = get_or_create_folder(drive, notebook_name, parent_id=parent_id)
    return root_id, notebook_id


# ── file uploads ──────────────────────────────────────────────────────────────

def upload_file(
    drive,
    *,
    name: str,
    data: bytes,
    mime_type: str,
    parent_id: str,
    description: str | None = None,
    modified_time: datetime | None = None,
) -> str:
    """Upload raw bytes to Drive. Returns the new file's ID."""
    metadata: dict[str, Any] = {"name": name, "parents": [parent_id]}
    if description:
        metadata["description"] = description
    if modified_time:
        metadata["modifiedTime"] = modified_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=True)
    file = _retry(
        drive.files()
        .create(body=metadata, media_body=media, fields="id")
        .execute
    )
    return file["id"]


def file_exists(drive, name: str, parent_id: str) -> str | None:
    """Return file ID if a file named `name` already exists in `parent_id`, else None."""
    q = (
        f"name = {_quote(name)} and '{parent_id}' in parents"
        " and trashed = false"
    )
    resp = _retry(
        drive.files().list(q=q, fields="files(id)", spaces="drive").execute
    )
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def make_description(note_created: datetime | None, source_url: str | None) -> str:
    parts: list[str] = []
    if note_created:
        parts.append(f"Created: {note_created.strftime('%Y-%m-%d %H:%M UTC')}")
    if source_url:
        parts.append(f"Source: {source_url}")
    return "\n".join(parts)


def drive_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def doc_url(file_id: str) -> str:
    return f"https://docs.google.com/document/d/{file_id}/edit"


# ── internal ──────────────────────────────────────────────────────────────────

def _quote(s: str) -> str:
    """Escape a string for use in Drive API query expressions."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"

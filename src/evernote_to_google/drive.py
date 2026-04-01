"""
Google Drive API operations: folder management and file uploads.
"""

from __future__ import annotations

import io
import logging
import re
import time
from datetime import datetime
from typing import Any

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

_log = logging.getLogger(__name__)

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
            _log.debug(
                "API error %s — retrying in %.0fs (attempt %d/%d)",
                exc.status_code, delay, attempt + 1, _MAX_RETRIES,
            )
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

    _log.debug("going to query folder %r in parent %s (files.list)", name, parent_id or "root")
    resp = _retry(
        drive.files().list(q=q, fields="files(id, name)", spaces="drive").execute
    )
    files = resp.get("files", [])
    if files:
        _log.debug("folder %r found (id: %s)", name, files[0]["id"])
        return files[0]["id"]

    _log.debug("going to create folder %r (files.create)", name)
    metadata: dict[str, Any] = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    folder = _retry(
        drive.files().create(body=metadata, fields="id").execute
    )
    _log.debug("folder %r created (id: %s)", name, folder["id"])
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

    _log.debug("going to upload file %r [%s, %s bytes] (files.create)", name, mime_type, f"{len(data):,}")
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=True)
    file = _retry(
        drive.files()
        .create(body=metadata, media_body=media, fields="id")
        .execute
    )
    _log.debug("file %r uploaded (id: %s)", name, file["id"])
    return file["id"]


def file_exists(drive, name: str, parent_id: str) -> str | None:
    """Return file ID if a file named `name` already exists in `parent_id`, else None."""
    q = (
        f"name = {_quote(name)} and '{parent_id}' in parents"
        " and trashed = false"
    )
    _log.debug("going to check if file %r exists in folder %s (files.list)", name, parent_id)
    resp = _retry(
        drive.files().list(q=q, fields="files(id)", spaces="drive").execute
    )
    files = resp.get("files", [])
    result = files[0]["id"] if files else None
    _log.debug("file %r %s", name, f"found (id: {result})" if result else "not found")
    return result


def list_folder_files(drive, parent_id: str) -> set[str]:
    """Return the set of all file names in parent_id (non-trashed, paginated)."""
    _log.debug("going to list all files in folder %s (files.list)", parent_id)
    q = f"'{parent_id}' in parents and trashed = false"
    names: set[str] = set()
    page_token: str | None = None
    while True:
        kwargs: dict = dict(q=q, fields="nextPageToken, files(name)", spaces="drive", pageSize=1000)
        if page_token:
            kwargs["pageToken"] = page_token
        resp = _retry(drive.files().list(**kwargs).execute)
        for f in resp.get("files", []):
            names.add(f["name"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    _log.debug("folder %s contains %d files", parent_id, len(names))
    return names


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


# ── batch operations ──────────────────────────────────────────────────────────

def batch_set_permissions(drive, file_ids: list[str]) -> None:
    """
    Set 'anyone reader' permission on all file_ids in a single batch request.
    Individual failures are logged as warnings but do not raise.
    Note: Drive batch requests support up to 100 sub-requests.
    """
    errors: dict[str, str] = {}

    def _cb(request_id: str, response, exception) -> None:
        if exception:
            errors[request_id] = str(exception)

    batch = drive.new_batch_http_request(callback=_cb)
    for fid in file_ids:
        batch.add(
            drive.permissions().create(
                fileId=fid,
                body={"role": "reader", "type": "anyone"},
            ),
            request_id=fid,
        )
    batch.execute()
    for fid, err in errors.items():
        _log.warning("batch permission failed for %s: %s", fid, err)


def batch_delete_files(drive, file_ids: list[str]) -> None:
    """
    Delete all file_ids in a single batch request (best-effort cleanup).
    Individual failures are logged as warnings but do not raise.
    Note: Drive batch requests support up to 100 sub-requests.
    """
    errors: dict[str, str] = {}

    def _cb(request_id: str, response, exception) -> None:
        if exception:
            errors[request_id] = str(exception)

    batch = drive.new_batch_http_request(callback=_cb)
    for fid in file_ids:
        batch.add(drive.files().delete(fileId=fid), request_id=fid)
    batch.execute()
    for fid, err in errors.items():
        _log.warning("batch delete failed for %s: %s", fid, err)


# ── internal ──────────────────────────────────────────────────────────────────

def _quote(s: str) -> str:
    """Escape a string for use in Drive API query expressions."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"

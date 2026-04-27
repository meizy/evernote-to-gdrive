"""
Google Drive file uploads, listing, metadata formatting, and batch operations.
"""

from __future__ import annotations

import io
import logging
import time
from datetime import datetime
from typing import Any

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from .classifier import format_tags
from .display import rtl_display
from .drive_retry import (
    _MAX_RETRIES,
    _RETRY_STATUS,
    _WRITE_INTERVAL,
    _retry,
    _write_retry,
    add_bytes_uploaded,
)

_log = logging.getLogger(__name__)


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
        metadata["modifiedTime"] = _format_mtime(modified_time)

    _log.debug("going to upload file %s [%s, %s bytes] (files.create)", rtl_display(name), mime_type, f"{len(data):,}")
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=True)
    file = _write_retry(
        drive.files()
        .create(body=metadata, media_body=media, fields="id")
        .execute,
        op=f"upload '{name}'",
    )
    add_bytes_uploaded(len(data))
    _log.debug("file %s uploaded (id: %s)", rtl_display(name), file["id"])
    return file["id"]


def _list_folder_files_pairs(drive, parent_id: str) -> list[tuple[str, str]]:
    """Return all (name, file_id) pairs in parent_id, excluding trashed files, preserving duplicates."""
    _log.debug("going to list all files with IDs in folder %s (files.list)", parent_id)
    q = f"'{parent_id}' in parents and trashed = false"
    result: list[tuple[str, str]] = []
    page_token: str | None = None
    while True:
        kwargs: dict = dict(q=q, fields="nextPageToken, files(name, id)", spaces="drive", pageSize=1000)
        if page_token:
            kwargs["pageToken"] = page_token
        resp = _retry(drive.files().list(**kwargs).execute, op=f"list folder '{parent_id}'")
        for f in resp.get("files", []):
            result.append((f["name"], f["id"]))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    _log.debug("folder %s contains %d files", parent_id, len(result))
    return result



def list_folder_files(drive, parent_id: str) -> set[str]:
    """Return the set of all file names in parent_id (non-trashed)."""
    return {name for name, _ in _list_folder_files_pairs(drive, parent_id)}


def list_folder_files_all(drive, parent_id: str) -> list[str]:
    """Return all file names in parent_id as a list, preserving duplicates."""
    return [name for name, _ in _list_folder_files_pairs(drive, parent_id)]


# ── metadata helpers ──────────────────────────────────────────────────────────

def make_description(
    note_created: datetime | None,
    note_updated: datetime | None,
    source_url: str | None,
    tags: list[str] | None = None,
) -> str:
    parts: list[str] = []
    if note_created:
        parts.append(f"Created: {note_created.strftime('%Y-%m-%d %H:%M UTC')}")
    if note_updated:
        parts.append(f"Updated: {note_updated.strftime('%Y-%m-%d %H:%M UTC')}")
    if source_url:
        parts.append(f"Source: {source_url}")
    if tags:
        parts.append(format_tags(tags))
    return "\n".join(parts)


def _format_mtime(dt: datetime) -> str:
    """Format a datetime as the RFC 3339 string expected by the Drive API."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def drive_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def gdoc_url(file_id: str) -> str:
    return f"https://docs.google.com/document/d/{file_id}/edit"


def drive_image_url(file_id: str) -> str:
    return f"https://drive.google.com/uc?export=download&id={file_id}"


# ── batch operations ──────────────────────────────────────────────────────────

def _batch_with_retry(drive, file_ids: list[str], make_request, op_name: str) -> None:
    """
    Execute a batch Drive request for each file_id with exponential-backoff retry.

    make_request(drive, fid) must return an un-executed Drive API request object.
    Retries transient failures up to _MAX_RETRIES times.
    Note: Drive batch requests support up to 100 sub-requests.
    """
    if len(file_ids) > 100:
        _log.warning(
            "%s: %d items exceed the 100-request limit — truncating; %d items will be skipped",
            op_name, len(file_ids), len(file_ids) - 100,
        )
        file_ids = file_ids[:100]

    pending = list(file_ids)
    delay = 1.0
    for attempt in range(_MAX_RETRIES):
        errors: dict[str, HttpError] = {}

        def _cb(request_id: str, response, exception) -> None:
            if isinstance(exception, HttpError):
                errors[request_id] = exception

        batch = drive.new_batch_http_request(callback=_cb)
        for fid in pending:
            batch.add(make_request(drive, fid), request_id=fid)
        time.sleep(len(pending) * _WRITE_INTERVAL)
        batch.execute()

        retryable = {fid: exc for fid, exc in errors.items() if exc.status_code in _RETRY_STATUS}
        permanent = {fid: exc for fid, exc in errors.items() if exc.status_code not in _RETRY_STATUS}

        for fid, exc in permanent.items():
            _log.warning("%s failed for %s: %s", op_name, fid, exc)

        if not retryable:
            return

        _log.debug(
            "%s: %d transient errors (attempt %d/%d) — retrying in %.0fs",
            op_name, len(retryable), attempt + 1, _MAX_RETRIES, delay,
        )
        time.sleep(delay)
        delay *= 2
        pending = list(retryable.keys())

    for fid in pending:
        _log.warning("%s failed for %s after %d attempts", op_name, fid, _MAX_RETRIES)


def batch_set_permissions(drive, file_ids: list[str]) -> None:
    """Set 'anyone reader' permission on all file_ids in a single batch request."""
    _batch_with_retry(
        drive, file_ids,
        lambda d, fid: d.permissions().create(fileId=fid, body={"role": "reader", "type": "anyone"}),
        "batch permission",
    )


def batch_delete_files(drive, file_ids: list[str]) -> None:
    """Delete all file_ids in a single batch request (best-effort cleanup)."""
    _batch_with_retry(
        drive, file_ids,
        lambda d, fid: d.files().delete(fileId=fid),
        "batch delete",
    )

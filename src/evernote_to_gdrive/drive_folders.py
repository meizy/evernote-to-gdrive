"""
Google Drive folder lookup and creation helpers.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .display import rtl_display
from .drive_retry import _retry, _write_retry

_log = logging.getLogger(__name__)


def _quote(s: str) -> str:
    """Escape a string for use in Drive API query expressions."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def find_folder(drive, name: str, parent_id: str | None = None) -> str | None:
    """Return the Drive folder ID for `name` if it exists, else None. No creation."""
    q = f"name = {_quote(name)} and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    _log.debug("going to query folder %s in parent %s (files.list)", rtl_display(name), parent_id or "root")
    resp = _retry(
        drive.files().list(q=q, fields="files(id, name)", spaces="drive").execute,
        op=f"list folder '{name}'",
    )
    files = resp.get("files", [])
    if files:
        _log.debug("folder %s found (id: %s)", rtl_display(name), files[0]["id"])
        return files[0]["id"]
    return None


def get_or_create_folder(drive, name: str, parent_id: str | None = None) -> str:
    """Return the Drive folder ID for `name`, creating it if needed."""
    folder_id = find_folder(drive, name, parent_id=parent_id)
    if folder_id:
        return folder_id
    _log.debug("going to create folder %s (files.create)", rtl_display(name))
    metadata: dict[str, Any] = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]
    folder = _write_retry(
        drive.files().create(body=metadata, fields="id").execute,
        op=f"create folder '{name}'",
    )
    _log.debug("folder %s created (id: %s)", rtl_display(name), folder["id"])
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


def find_folder_path(drive, root_name: str, notebook_name: str, stack: str | None = None) -> str | None:
    """Read-only counterpart to ensure_folder_path. Returns notebook_id or None if any
    segment is missing. Never creates folders."""
    parts = [p for p in re.split(r"[/\\]", root_name) if p]
    parent_id: str | None = None
    for part in parts:
        parent_id = find_folder(drive, part, parent_id=parent_id)
        if parent_id is None:
            return None
    if stack:
        parent_id = find_folder(drive, stack, parent_id=parent_id)
        if parent_id is None:
            return None
    return find_folder(drive, notebook_name, parent_id=parent_id)

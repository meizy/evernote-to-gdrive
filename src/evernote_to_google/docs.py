"""
Google Docs API operations: create a document from HTML.

Strategy:
  1. Upload the HTML body (pre-processed ENML) to Drive with conversion to
     application/vnd.google-apps.document.  Drive handles all formatting
     (headings, bold, links, inline images) natively.
  2. Restore modifiedTime via files().update().
     (Drive resets modifiedTime during import.)
"""

from __future__ import annotations

import logging
from typing import Any

from googleapiclient.http import MediaInMemoryUpload

from .display import rtl_display
from .drive import _write_retry, add_bytes_uploaded

_log = logging.getLogger(__name__)


def create_doc(
    drive,
    *,
    title: str,
    html: bytes,
    parent_id: str,
    description: str | None = None,
    modified_time=None,
) -> str:
    """
    Import HTML as a Google Doc via Drive conversion. Returns the Doc file ID.
    """
    metadata: dict[str, Any] = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
        "parents": [parent_id],
    }
    if description:
        metadata["description"] = description
    if modified_time:
        metadata["modifiedTime"] = modified_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    _log.debug("going to create gdoc %r (files.create)", rtl_display(title))
    media = MediaInMemoryUpload(html, mimetype="text/html", resumable=False)
    file = _write_retry(drive.files().create(body=metadata, media_body=media, fields="id").execute, op=f"create doc '{title}'")
    doc_id = file["id"]
    add_bytes_uploaded(len(html))
    _log.debug("gdoc %r created successfully (id: %s)", rtl_display(title), doc_id)

    # Drive resets modifiedTime during import — patch it back (best-effort).
    if modified_time:
        _log.debug("going to restore modifiedTime for doc %s (files.update)", doc_id)
        _write_retry(
            drive.files()
            .update(
                fileId=doc_id,
                body={"modifiedTime": modified_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
                fields="id",
            )
            .execute,
            op=f"set modifiedTime for doc '{title}'",
        )
        _log.debug("modifiedTime restored for doc %s", doc_id)

    return doc_id


def update_doc(drive, doc_id: str, html: bytes, modified_time=None) -> None:
    """
    Replace a Google Doc's content by re-importing HTML.
    Patches modifiedTime back after import (Drive resets it).
    """
    from googleapiclient.http import MediaInMemoryUpload
    _log.debug("going to update gdoc %s content (files.update)", doc_id)
    media = MediaInMemoryUpload(html, mimetype="text/html", resumable=False)
    _write_retry(
        drive.files().update(
            fileId=doc_id,
            media_body=media,
            fields="id",
        ).execute,
        op=f"update doc '{doc_id}'",
    )
    add_bytes_uploaded(len(html))
    _log.debug("gdoc %s content updated", doc_id)

    if modified_time:
        _log.debug("going to restore modifiedTime for doc %s (files.update)", doc_id)
        _write_retry(
            drive.files().update(
                fileId=doc_id,
                body={"modifiedTime": modified_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
                fields="id",
            ).execute,
            op=f"set modifiedTime for doc '{doc_id}'",
        )
        _log.debug("modifiedTime restored for doc %s", doc_id)

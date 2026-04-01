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

from .drive import _retry

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

    _log.debug("going to create gdoc %r (files.create)", title)
    media = MediaInMemoryUpload(html, mimetype="text/html", resumable=False)
    file = _retry(drive.files().create(body=metadata, media_body=media, fields="id").execute)
    doc_id = file["id"]
    _log.debug("gdoc %r created successfully (id: %s)", title, doc_id)

    # Drive resets modifiedTime during import — patch it back (best-effort).
    if modified_time:
        _log.debug("going to restore modifiedTime for doc %s (files.update)", doc_id)
        _retry(
            drive.files()
            .update(
                fileId=doc_id,
                body={"modifiedTime": modified_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
                fields="id",
            )
            .execute
        )
        _log.debug("modifiedTime restored for doc %s", doc_id)

    return doc_id

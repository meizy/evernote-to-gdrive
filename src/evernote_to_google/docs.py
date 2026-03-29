"""
Google Docs API operations: create documents and insert content.

Strategy:
  1. Create an empty Google Doc via the Drive API (gives us a file ID).
  2. Use the Docs API batchUpdate to insert text and inline images.
  3. PDFs are uploaded as separate Drive files and their links appended to the doc.

Insertion order matters: the Docs API uses character indices. We build a list
of "segments" (text or image) in document order, then insert them in reverse
order so that earlier insertions don't shift later indices (or we always insert
at index 1, pushing content down).

We use the "insert at index 1" approach for simplicity: insert the last segment
first, each time at index 1, so the document ends up in the original order.
"""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass
from typing import Any

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from .classifier import attachment_drive_filename
from .drive import drive_url, upload_file, _retry
from .parser import Attachment, Note

# Supported MIME types for inline image embedding
_EMBEDDABLE_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif"}

# Unicode ranges that indicate RTL scripts (Hebrew, Arabic, etc.)
_RTL_RANGES = [
    (0x0590, 0x05FF),  # Hebrew
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0xFB1D, 0xFDFF),  # Hebrew/Arabic Presentation Forms
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
]


def _is_rtl(text: str) -> bool:
    """Return True if the text contains RTL characters (Hebrew, Arabic, etc.)."""
    for ch in text:
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in _RTL_RANGES):
            return True
    return False


@dataclass
class _TextSegment:
    text: str


@dataclass
class _ImageSegment:
    attachment: Attachment
    index: int  # 1-based position among attachments


@dataclass
class _LinkSegment:
    label: str
    url: str


# ── public API ────────────────────────────────────────────────────────────────

def create_doc(
    drive,
    docs,
    *,
    title: str,
    plain_text: str,
    note: Note,
    attachments: list[Attachment],
    parent_id: str,
    description: str | None = None,
    modified_time=None,
) -> str:
    """
    Create a Google Doc with `plain_text` as body content.
    Embeds JPEG/PNG/GIF attachments inline; uploads PDFs to Drive and inserts links.
    Returns the Doc file ID.
    """
    # Step 1: create empty doc via Drive (so we can set the parent folder)
    metadata: dict[str, Any] = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
        "parents": [parent_id],
    }
    if description:
        metadata["description"] = description
    if modified_time:
        metadata["modifiedTime"] = modified_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    file = _retry(drive.files().create(body=metadata, fields="id").execute)
    doc_id = file["id"]

    # Step 2: build update requests
    requests = _build_requests(
        drive=drive,
        docs=docs,
        doc_id=doc_id,
        plain_text=plain_text,
        note=note,
        attachments=attachments,
        parent_id=parent_id,
    )

    if requests:
        _retry(docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute)

    # Apply RTL direction if the title or body contains RTL characters.
    # Must be done after content insertion so we know the document's end index.
    if _is_rtl(title) or _is_rtl(plain_text):
        _apply_rtl(docs, doc_id)

    return doc_id


def create_attachment_index_doc(
    drive,
    docs,
    *,
    title: str,
    note: Note,
    attachments: list[Attachment],
    parent_id: str,
    description: str | None = None,
    modified_time=None,
) -> str:
    """
    Create a Google Doc that only lists attachment links (for multi-attachment,
    no-text notes when --multi-attachment=doc).
    """
    return create_doc(
        drive=drive,
        docs=docs,
        title=title,
        plain_text="",
        note=note,
        attachments=attachments,
        parent_id=parent_id,
        description=description,
        modified_time=modified_time,
    )


# ── internals ─────────────────────────────────────────────────────────────────

def _build_requests(
    drive, docs, doc_id: str, plain_text: str, note: Note, attachments: list[Attachment], parent_id: str
) -> list[dict]:
    """
    Build a list of Docs API batchUpdate requests that populate the document.

    We insert everything at index 1 in reverse segment order so the doc ends
    up reading top-to-bottom:
      [optional source URL header]
      [plain text body]
      [attachment 1: inline image OR link]
      [attachment 2: ...]
      ...
    """
    segments: list[_TextSegment | _ImageSegment | _LinkSegment] = []

    # Header: source URL
    if note.source_url:
        segments.append(_TextSegment(f"Source: {note.source_url}\n\n"))

    # Body text
    if plain_text:
        segments.append(_TextSegment(plain_text + "\n"))

    # Attachments
    for i, att in enumerate(attachments, start=1):
        if att.mime in _EMBEDDABLE_IMAGE_MIME:
            segments.append(_ImageSegment(attachment=att, index=i))
        else:
            # Upload to Drive, then insert a link
            filename = attachment_drive_filename(note.title, i, att)
            file_id = upload_file(
                drive,
                name=filename,
                data=att.data,
                mime_type=att.mime,
                parent_id=parent_id,
            )
            url = drive_url(file_id)
            segments.append(_LinkSegment(label=f"[{filename}]", url=url))

    # Build Docs API requests by inserting segments in reverse at index 1
    requests: list[dict] = []
    for seg in reversed(segments):
        if isinstance(seg, _TextSegment):
            requests.extend(_insert_text_requests(seg.text))
        elif isinstance(seg, _ImageSegment):
            requests.extend(_insert_image_requests(seg.attachment))
        elif isinstance(seg, _LinkSegment):
            requests.extend(_insert_link_requests(seg.label, seg.url))

    return requests


def _insert_text_requests(text: str) -> list[dict]:
    return [{"insertText": {"location": {"index": 1}, "text": text}}]


def _insert_image_requests(att: Attachment) -> list[dict]:
    # The Docs API requires a publicly accessible URI for insertInlineImage.
    # We use a data URI as a workaround — note: this only works for small images
    # and is not officially documented but works in practice for JPEG/PNG/GIF
    # up to a few MB. For production, uploading to a public GCS bucket and
    # using that URL would be more reliable.
    b64 = base64.b64encode(att.data).decode()
    uri = f"data:{att.mime};base64,{b64}"
    return [
        {
            "insertInlineImage": {
                "location": {"index": 1},
                "uri": uri,
                "objectSize": {
                    "width": {"magnitude": 400, "unit": "PT"},
                    "height": {"magnitude": 400, "unit": "PT"},
                },
            }
        }
    ]


def _apply_rtl(docs, doc_id: str) -> None:
    """Set all paragraphs in the document to RIGHT_TO_LEFT direction."""
    doc = _retry(docs.documents().get(documentId=doc_id, fields="body.content").execute)
    body_content = doc.get("body", {}).get("content", [])
    if not body_content:
        return
    end_index = body_content[-1].get("endIndex", 2) - 1
    if end_index <= 1:
        return
    _retry(
        docs.documents()
        .batchUpdate(
            documentId=doc_id,
            body={
                "requests": [
                    {
                        "updateParagraphStyle": {
                            "range": {"startIndex": 1, "endIndex": end_index},
                            "paragraphStyle": {"direction": "RIGHT_TO_LEFT"},
                            "fields": "direction",
                        }
                    }
                ]
            },
        )
        .execute
    )


def _insert_link_requests(label: str, url: str) -> list[dict]:
    text = f"{label}\n"
    return [
        {"insertText": {"location": {"index": 1}, "text": text}},
        {
            "updateTextStyle": {
                "range": {"startIndex": 1, "endIndex": 1 + len(label)},
                "textStyle": {"link": {"url": url}},
                "fields": "link",
            }
        },
    ]

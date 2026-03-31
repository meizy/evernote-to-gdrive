"""
Google Docs API operations: create documents and insert content.

Strategy:
  1. Create an empty Google Doc via the Drive API (gives us a file ID).
  2. Use the Docs API batchUpdate to insert text and inline images.
  3. All attachments (images and PDFs/other) are pre-uploaded by the caller;
     their public URLs (images) and Drive view URLs (others) are passed in.
  4. After all batchUpdate calls, restore modifiedTime via files().update().
     (batchUpdate resets modifiedTime to the current wall-clock time.)

Insertion order matters: the Docs API uses character indices. We insert
everything at index 1 in reverse segment order so the document ends up
reading top-to-bottom.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

try:
    from PIL import Image as _PIL_Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

from .classifier import _is_rtl
from .drive import _retry
from .parser import Note


# ── public segment types (constructed by callers) ─────────────────────────────

@dataclass
class DocImage:
    """Pre-resolved inline image: a publicly accessible URL with dimensions."""
    url: str
    width_pt: float
    height_pt: float


@dataclass
class DocLink:
    """Pre-resolved file link: a display label and a Drive view URL."""
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
    resolved_attachments: list,  # list[DocImage | DocLink], in note order
    parent_id: str,
    description: str | None = None,
    modified_time=None,
    body_segments: list | None = None,  # list[str | DocImage | DocLink], overrides plain_text + resolved_attachments
) -> str:
    """
    Create a Google Doc with plain_text as body and pre-resolved attachment references.
    DocImage entries are embedded inline; DocLink entries are inserted as hyperlinks.
    Returns the Doc file ID.
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

    file = _retry(drive.files().create(body=metadata, fields="id").execute)
    doc_id = file["id"]

    requests = _build_requests(plain_text, note, resolved_attachments, body_segments=body_segments)
    if requests:
        _retry(docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute)

    if _is_rtl(title) or _is_rtl(plain_text):
        _apply_rtl(docs, doc_id)

    # batchUpdate (and _apply_rtl) reset modifiedTime — patch it back (best-effort).
    if modified_time:
        _retry(
            drive.files()
            .update(
                fileId=doc_id,
                body={"modifiedTime": modified_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
                fields="id",
            )
            .execute
        )

    return doc_id


# ── internals ─────────────────────────────────────────────────────────────────

@dataclass
class _TextSegment:
    text: str


def _build_requests(
    plain_text: str,
    note: Note,
    resolved_attachments: list,
    body_segments: list | None = None,
) -> list[dict]:
    """
    Build a list of Docs API batchUpdate requests that populate the document.

    We insert everything at index 1 in reverse segment order so the doc ends
    up reading top-to-bottom:
      [optional source URL header]
      [body: either interleaved text+images (body_segments) or plain_text then attachments]

    body_segments, when provided, is list[str | DocImage | DocLink] in document order
    and is used instead of (plain_text, resolved_attachments) to preserve inline image positions.
    """
    segments: list[_TextSegment | DocImage | DocLink] = []

    if note.source_url:
        segments.append(_TextSegment(f"Source: {note.source_url}\n\n"))

    if body_segments is not None:
        for seg in body_segments:
            if isinstance(seg, str):
                segments.append(_TextSegment(seg + "\n" if not seg.endswith("\n") else seg))
            else:
                segments.append(seg)
    else:
        if plain_text:
            segments.append(_TextSegment(plain_text + "\n"))
        segments.extend(resolved_attachments)

    requests: list[dict] = []
    for seg in reversed(segments):
        if isinstance(seg, _TextSegment):
            requests.extend(_insert_text_requests(seg.text))
        elif isinstance(seg, DocImage):
            requests.extend(_insert_image_requests(seg))
        elif isinstance(seg, DocLink):
            requests.extend(_insert_link_requests(seg.label, seg.url))

    return requests


def _insert_text_requests(text: str) -> list[dict]:
    return [{"insertText": {"location": {"index": 1}, "text": text}}]


_MAX_IMG_W_PT = 400  # max image width in PT (~5.5" at 72 PT/in)
_MAX_IMG_H_PT = 560  # max image height in PT (~7.8" at 72 PT/in)
_DEFAULT_IMG_PT = 400  # fallback size when dimensions can't be read


def _image_size_pt(data: bytes) -> tuple[float, float]:
    """
    Return (width_pt, height_pt) scaled to fit within max bounds, preserving
    aspect ratio. Falls back to (_DEFAULT_IMG_PT, _DEFAULT_IMG_PT) if the
    dimensions cannot be determined.
    """
    w_px = h_px = None
    if _HAS_PIL:
        try:
            with _PIL_Image.open(io.BytesIO(data)) as img:
                w_px, h_px = img.size
        except Exception:
            pass

    if not w_px or not h_px:
        return _DEFAULT_IMG_PT, _DEFAULT_IMG_PT

    # Convert pixels to PT (assuming 96 DPI screen: 1px = 72/96 PT)
    w_pt = w_px * 72 / 96
    h_pt = h_px * 72 / 96

    scale = min(_MAX_IMG_W_PT / w_pt, _MAX_IMG_H_PT / h_pt, 1.0)
    return w_pt * scale, h_pt * scale


def _insert_image_requests(img: DocImage) -> list[dict]:
    return [
        {
            "insertInlineImage": {
                "location": {"index": 1},
                "uri": img.url,
                "objectSize": {
                    "width": {"magnitude": img.width_pt, "unit": "PT"},
                    "height": {"magnitude": img.height_pt, "unit": "PT"},
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

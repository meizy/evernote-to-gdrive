"""
Classify notes into migration categories and compute derived fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

import html2text

from .parser import Attachment, Note


class NoteKind(Enum):
    TEXT_ONLY = auto()             # text, no attachments → Google Doc
    ATTACHMENT_ONLY_SINGLE = auto()  # no text, 1 attachment → raw file
    ATTACHMENT_ONLY_MULTI = auto()   # no text, ≥2 attachments → depends on flag
    TEXT_WITH_ATTACHMENTS = auto()  # text + ≥1 attachment → Google Doc + files


@dataclass
class ClassifiedNote:
    note: Note
    kind: NoteKind
    plain_text: str  # stripped body text (may be empty)


# ── helpers ───────────────────────────────────────────────────────────────────

_h2t = html2text.HTML2Text()
_h2t.ignore_links = False
_h2t.ignore_images = True
_h2t.body_width = 0  # no wrapping


def enml_to_text(enml: str) -> str:
    """Convert ENML (XHTML) to plain text. Returns empty string if nothing meaningful."""
    if not enml:
        return ""
    try:
        text = _h2t.handle(enml)
    except Exception:
        return ""
    return text.strip()


def has_meaningful_text(plain_text: str) -> bool:
    return bool(plain_text)


# ── public API ─────────────────────────────────────────────────────────────────

def classify(note: Note) -> ClassifiedNote:
    plain_text = enml_to_text(note.enml)
    has_text = has_meaningful_text(plain_text)
    n_attachments = len(note.attachments)

    if has_text and n_attachments == 0:
        kind = NoteKind.TEXT_ONLY
    elif has_text and n_attachments >= 1:
        kind = NoteKind.TEXT_WITH_ATTACHMENTS
    elif not has_text and n_attachments == 1:
        kind = NoteKind.ATTACHMENT_ONLY_SINGLE
    else:
        # no text, 0 attachments → treat as empty text-only doc; also covers multi
        kind = NoteKind.ATTACHMENT_ONLY_MULTI if n_attachments >= 2 else NoteKind.TEXT_ONLY

    return ClassifiedNote(note=note, kind=kind, plain_text=plain_text)


def attachment_drive_filename(note_title: str, index: int, attachment: Attachment) -> str:
    """
    Return the Drive filename for a separately-uploaded attachment.
    Pattern: <note_title>_<n>.<ext>
    Index is 1-based.
    """
    ext = _ext_for_mime(attachment.mime)
    safe_title = _safe_filename(note_title)
    return f"{safe_title}_{index}{ext}"


def _ext_for_mime(mime: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
    }
    return mapping.get(mime.lower(), "")


def _safe_filename(name: str, max_length: int = 200) -> str:
    """Strip characters that are problematic in Drive filenames."""
    # Drive allows most characters, but avoid / \ : * ? " < > |
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip()[:max_length]

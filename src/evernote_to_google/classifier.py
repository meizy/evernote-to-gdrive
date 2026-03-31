"""
Classify notes into migration categories and compute derived fields.
"""

from __future__ import annotations

import re
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
    attachments: list[Attachment] = None  # note.attachments minus unnamed octet-stream blobs
    # Ordered list of interleaved text chunks (str) and Attachment objects, derived from
    # ENML <en-media> tag positions. None when there are no inline attachments.
    segments: list | None = None

    def __post_init__(self):
        if self.attachments is None:
            self.attachments = self.note.attachments


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


def enml_to_segments(enml: str, attachments: list[Attachment]) -> list:
    """
    Split ENML into an ordered list of str (HTML chunks) and Attachment objects,
    preserving the inline position of each <en-media> tag.

    Returns list[str | Attachment]. Text chunks are raw HTML; callers should
    convert them with enml_to_text before use.
    """
    if not enml or not attachments:
        return [enml] if enml else []

    hash_to_att = {att.hash: att for att in attachments if att.hash}
    if not hash_to_att:
        return [enml]

    # Split at self-closing <en-media .../> tags, capturing the tag itself
    parts = re.split(r'(<en-media\b[^>]*/?>)', enml)

    segments: list = []
    for part in parts:
        m = re.match(r'<en-media\b', part)
        if m:
            hash_match = re.search(r'\bhash="([0-9a-fA-F]+)"', part)
            if hash_match:
                att = hash_to_att.get(hash_match.group(1))
                if att:
                    segments.append(att)
                # else: unresolvable hash (e.g. octet-stream filtered out) — skip
        else:
            segments.append(part)

    return segments


def has_meaningful_text(plain_text: str) -> bool:
    return bool(plain_text)


# ── public API ─────────────────────────────────────────────────────────────────

def classify(note: Note) -> ClassifiedNote:
    plain_text = enml_to_text(note.enml)
    has_text = has_meaningful_text(plain_text)

    # Strip application/octet-stream attachments — these are raw HTML sources
    # or other internal blobs saved by the Evernote web clipper and have no
    # meaningful content for migration.
    attachments = [
        att for att in note.attachments
        if att.mime != "application/octet-stream"
    ]

    n_attachments = len(attachments)

    if has_text and n_attachments == 0:
        kind = NoteKind.TEXT_ONLY
    elif has_text and n_attachments >= 1:
        kind = NoteKind.TEXT_WITH_ATTACHMENTS
    elif not has_text and n_attachments == 1:
        kind = NoteKind.ATTACHMENT_ONLY_SINGLE
    else:
        # no text, 0 attachments → treat as empty text-only doc; also covers multi
        kind = NoteKind.ATTACHMENT_ONLY_MULTI if n_attachments >= 2 else NoteKind.TEXT_ONLY

    # Build ordered interleaved segments when there are inline attachments
    segments = None
    if attachments and note.enml:
        raw_segs = enml_to_segments(note.enml, attachments)
        # Only use segments when at least one Attachment appears inline
        if any(isinstance(s, Attachment) for s in raw_segs):
            segments = raw_segs

    return ClassifiedNote(note=note, kind=kind, plain_text=plain_text, attachments=attachments, segments=segments)


# ── mime helpers ───────────────────────────────────────────────────────────────

# Known application/* labels that can't be cleanly derived from the subtype
_MIME_LABEL_MAP: dict[str, str] = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "doc",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xls",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "ppt",
    "application/octet-stream": "bin",
    "application/x-zip-compressed": "zip",
    "application/x-rar-compressed": "rar",
    "application/x-tar": "tar",
}

_MIME_EXT_MAP: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/webm": ".webm",
    "text/plain": ".txt",
    "text/html": ".html",
    "text/csv": ".csv",
    "text/markdown": ".md",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/x-rar-compressed": ".rar",
    "application/x-tar": ".tar",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/rtf": ".rtf",
    "application/json": ".json",
    "application/xml": ".xml",
}


def attachment_label(mime: str) -> str:
    """
    Return a short label for a MIME type, used in sibling filenames.
    Labels by primary type: image→img, audio→aud, video→vid, text→txt.
    application/pdf→pdf; other application/*: derived from subtype (≤3 chars).
    """
    mime = mime.lower()
    primary, _, subtype = mime.partition("/")
    if primary == "image":
        return "img"
    if primary == "audio":
        return "aud"
    if primary == "video":
        return "vid"
    if primary == "text":
        return "txt"
    if mime in _MIME_LABEL_MAP:
        return _MIME_LABEL_MAP[mime]
    # Derive from subtype: strip vnd. prefix, take first word segment
    sub = subtype
    if sub.startswith("vnd."):
        sub = sub[4:]
    sub = re.split(r"[.\-+]", sub)[0]
    return sub[:3] if sub else "att"


def attachment_ext(mime: str) -> str:
    """Return the file extension (with dot) for a MIME type, or '' if unknown."""
    return _MIME_EXT_MAP.get(mime.lower(), "")


# Backward-compatible alias used internally
_ext_for_mime = attachment_ext


def attachment_sibling_filename(note_title: str, label: str, type_index: int, attachment: Attachment) -> str:
    """
    Return the filename for a sibling attachment file.
    Pattern: <safe_title>_<label>_<n>.<ext>
    label is the mime-type label (img, pdf, aud, etc.); type_index is 1-based per label.
    """
    ext = attachment_ext(attachment.mime)
    safe_title = _safe_name(note_title)
    return f"{safe_title}_{label}_{type_index}{ext}"


def _safe_name(name: str, max_length: int = 200) -> str:
    """Strip characters that are problematic in Drive filenames."""
    # Drive allows most characters, but avoid / \ : * ? " < > |
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip()[:max_length]


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


# Supported MIME types for inline image embedding
_EMBEDDABLE_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif"}

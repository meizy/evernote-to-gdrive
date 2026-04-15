"""
Classify notes into migration categories and compute derived fields.
"""

from __future__ import annotations

import os

from dataclasses import dataclass
from enum import Enum, auto
from typing import Iterable

from ._enml import enml_to_text
from .models import AttachmentPolicy
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

    def __post_init__(self):
        if self.attachments is None:
            self.attachments = self.note.attachments


# Strip attachments with unsupported or noise mime types:
# - application/octet-stream: raw HTML sources or internal blobs from the
#   Evernote web clipper with no meaningful content for migration.
# - image/svg+xml: SVGs are not supported in Google Docs or DOCX and are
#   typically decorative web-clip chrome (site logos, icons).
_SKIP_MIME = {"application/octet-stream", "image/svg+xml"}


# ── public API ─────────────────────────────────────────────────────────────────

def classify(note: Note) -> ClassifiedNote:
    plain_text = enml_to_text(note.enml)
    has_text = bool(plain_text)
    attachments = [
        att for att in note.attachments
        if att.mime not in _SKIP_MIME
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

    return ClassifiedNote(note=note, kind=kind, plain_text=plain_text, attachments=attachments)


# ── mime helpers ───────────────────────────────────────────────────────────────


_MIME_EXT_MAP: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
    "image/svg+xml": ".svg",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/amr": ".amr",
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

_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}



def attachment_ext(mime: str) -> str:
    """Return the file extension (with dot) for a MIME type.
    Uses the lookup table for known types; falls back to the MIME subtype
    (stripping x- prefix and +suffix, e.g. image/x-bmp → .bmp, image/svg+xml → .svg).
    Returns '' only if the subtype is empty or unparseable.
    """
    mime = mime.lower()
    if mime in _MIME_EXT_MAP:
        return _MIME_EXT_MAP[mime]
    _, _, subtype = mime.partition("/")
    if subtype.startswith("x-"):
        subtype = subtype[2:]
    subtype = subtype.split("+")[0].split(".")[0]
    return f".{subtype}" if subtype else ""


def ensure_extension(name: str, mime_type: str) -> str:
    """Append the MIME-based extension to *name* unless it already has one."""
    ext = attachment_ext(mime_type)
    if ext and not name.endswith(ext):
        return f"{name}{ext}"
    return name


def attachment_sibling_filename(note_title: str, index: int, attachment: Attachment) -> str:
    """
    Return the filename for a non-image sibling attachment file.
    Pattern: <safe_title>_<n>.<ext>  (single global running sequence, 1-based)
    """
    ext = attachment_ext(attachment.mime)
    safe_title = safe_drive_name(note_title)
    return f"{safe_title}_{index}{ext}"


def image_temp_filename(note_title: str, index: int, attachment: Attachment) -> str:
    """
    Return the temporary upload name for an embedded image in gdrive mode.
    Pattern: temp_<safe_title>_<n>.<ext>
    The temp_ prefix ensures these files are never matched by note_exists
    (which checks safe_title*). Deleted after embedding; orphans are identifiable.
    """
    ext = attachment_ext(attachment.mime)
    safe_title = safe_drive_name(note_title)
    return f"temp_{safe_title}_{index}{ext}"


def sanitize_name(name: str) -> str:
    """Replace characters that are invalid in filenames with underscores."""
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "_")
    return name


def safe_drive_name(name: str, max_length: int = 200) -> str:
    """Normalize a name for Drive and generic non-filesystem output."""
    return sanitize_name(name).strip()[:max_length]


def safe_local_name(name: str, max_length: int = 200) -> str:
    """Normalize a name for local filesystem output, including Windows rules."""
    cleaned = safe_drive_name(name, max_length=max_length)
    stem, suffix = os.path.splitext(cleaned)
    if suffix and not suffix.strip(". "):
        stem, suffix = cleaned, ""
    stem = stem.rstrip(". ")
    cleaned = f"{stem}{suffix}" if stem else suffix
    if not cleaned:
        cleaned = "_"
        stem = "_"
    elif not stem and suffix:
        cleaned = f"_{suffix}"
        stem = "_"

    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def _safe_name(name: str, max_length: int = 200) -> str:
    """Backward-compatible alias for Drive-style safe names."""
    return safe_drive_name(name, max_length=max_length)


# Unicode ranges that indicate RTL scripts (Hebrew, Arabic, etc.)
_RTL_RANGES = [
    (0x0590, 0x05FF),  # Hebrew
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0xFB1D, 0xFDFF),  # Hebrew/Arabic Presentation Forms
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
]


def _is_rtl(text: str) -> bool:
    """Return True if the text contains *any* RTL character (Hebrew, Arabic, etc.).

    Used for document/paragraph-level RTL detection in DOCX and web-clip output.
    For terminal display reversal use display.rtl_display() instead, which checks
    only the first word and uses Unicode bidi categories.
    """
    for ch in text:
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in _RTL_RANGES):
            return True
    return False


# Supported MIME types for inline image embedding
_EMBEDDABLE_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# Maximum image width in pixels — fits a standard Google Doc / docx page with margins
IMAGE_MAX_WIDTH_PX = 500


def format_tags(tags: list[str]) -> str:
    """Return tags as a bracketed string: '[tag:X, tag:Y]'."""
    return f"[{', '.join(f'tag:{t}' for t in tags)}]"


def _all_non_image(attachments: list[Attachment]) -> bool:
    """Return True if all attachments are non-embeddable (no images to embed)."""
    return not any(a.mime in _EMBEDDABLE_IMAGE_MIME for a in attachments)


def is_note_file(name: str, filename: str) -> bool:
    """Return True if filename matches name exactly, as name.ext, or name_suffix."""
    return filename == name or filename.startswith(f"{name}.") or filename.startswith(f"{name}_")


def note_name_matches(name: str, existing_names: Iterable[str]) -> bool:
    """Return True if `name` matches any entry in `existing_names`.

    Matches: bare name, name.<any ext>, or name_<any suffix> (siblings, _0 docs).
    Temp image files (temp_<name>_...) are excluded by the temp_ prefix convention.
    """
    return any(is_note_file(name, f) for f in existing_names)

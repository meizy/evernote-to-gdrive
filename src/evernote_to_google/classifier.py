"""
Classify notes into migration categories and compute derived fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from ._enml import enml_to_text
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


# ── helpers ───────────────────────────────────────────────────────────────────

def has_meaningful_text(plain_text: str) -> bool:
    return bool(plain_text)


# ── public API ─────────────────────────────────────────────────────────────────

def classify(note: Note) -> ClassifiedNote:
    plain_text = enml_to_text(note.enml)
    has_text = has_meaningful_text(plain_text)

    # Strip attachments with unsupported or noise mime types:
    # - application/octet-stream: raw HTML sources or internal blobs from the
    #   Evernote web clipper with no meaningful content for migration.
    # - image/svg+xml: SVGs are not supported in Google Docs or DOCX and are
    #   typically decorative web-clip chrome (site logos, icons).
    _SKIP_MIME = {"application/octet-stream", "image/svg+xml"}
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


# Backward-compatible alias used internally
_ext_for_mime = attachment_ext


def attachment_sibling_filename(note_title: str, index: int, attachment: Attachment) -> str:
    """
    Return the filename for a non-image sibling attachment file.
    Pattern: <safe_title>_<n>.<ext>  (single global running sequence, 1-based)
    """
    ext = attachment_ext(attachment.mime)
    safe_title = _safe_name(note_title)
    return f"{safe_title}_{index}{ext}"


def image_temp_filename(note_title: str, index: int, attachment: Attachment) -> str:
    """
    Return the temporary upload name for an embedded image in gdrive mode.
    Pattern: <safe_title>_img_<n>.<ext>
    These files are deleted after embedding. The _img_ infix makes orphaned
    files easy to identify and remove manually if cleanup fails.
    """
    ext = attachment_ext(attachment.mime)
    safe_title = _safe_name(note_title)
    return f"{safe_title}_img_{index}{ext}"


def sanitize_name(name: str) -> str:
    """Replace characters that are invalid in filenames with underscores."""
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "_")
    return name


def _safe_name(name: str, max_length: int = 200) -> str:
    """Strip characters that are problematic in Drive filenames."""
    return sanitize_name(name).strip()[:max_length]


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
_EMBEDDABLE_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}

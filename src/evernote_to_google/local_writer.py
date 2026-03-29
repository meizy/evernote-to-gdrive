"""
Local output mode: write notes to a folder/subfolder tree on disk.

  attachment-only, single  → raw file (<title>.<ext>)
  attachment-only, multi   → one .docx listing all attachments (doc mode)
                             OR one raw file per attachment (files mode)
  text-only                → <title>.docx
  text + attachments       → <title>.docx  (images embedded, PDFs as sibling files)

RTL (Hebrew/Arabic) paragraphs are detected and marked as bidi in the .docx XML
so that Word, LibreOffice, and Google Docs all render them correctly.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import ctypes.wintypes
import io
import os
import platform
import struct
import re
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches

from .classifier import NoteKind, attachment_drive_filename, ClassifiedNote
from .parser import Attachment, Note

# Unicode ranges that indicate RTL scripts
_RTL_RANGES = [
    (0x0590, 0x05FF),
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0xFB1D, 0xFDFF),
    (0xFE70, 0xFEFF),
]

_EMBEDDABLE_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif"}
_IMAGE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif"}


def _is_rtl(text: str) -> bool:
    for ch in text:
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in _RTL_RANGES):
            return True
    return False


def _set_para_rtl(paragraph) -> None:
    """Mark a python-docx paragraph as right-to-left."""
    pPr = paragraph._p.get_or_add_pPr()
    bidi = OxmlElement("w:bidi")
    bidi.set(qn("w:val"), "1")
    pPr.append(bidi)


def _add_paragraph(doc: Document, text: str) -> None:
    """Add a paragraph, marking it RTL if it contains RTL characters."""
    para = doc.add_paragraph(text)
    if _is_rtl(text):
        _set_para_rtl(para)


def _add_file_hyperlink(doc: Document, display_text: str, filename: str) -> None:
    """Add a paragraph with a clickable hyperlink to a sibling file."""
    paragraph = doc.add_paragraph()

    r_id = doc.part.relate_to(
        filename,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )

    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)

    run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    rStyle = OxmlElement("w:rStyle")
    rStyle.set(qn("w:val"), "Hyperlink")
    rPr.append(rStyle)
    run.append(rPr)

    text_elem = OxmlElement("w:t")
    text_elem.text = display_text
    run.append(text_elem)
    hyperlink.append(run)

    paragraph._p.append(hyperlink)

    if _is_rtl(display_text):
        _set_para_rtl(paragraph)


# ── filesystem timestamps ─────────────────────────────────────────────────────

def _set_timestamps(path: Path, created: datetime | None, updated: datetime | None) -> None:
    """
    Set file timestamps to match the original Evernote note dates.
      mtime → note's updated date (fallback: created)
      birth time (macOS only) → note's created date
    """
    mtime_dt = updated or created
    if mtime_dt:
        mtime = mtime_dt.timestamp()
        os.utime(path, (mtime, mtime))

    system = platform.system()
    if system == "Darwin" and created:
        _set_macos_birthtime(path, created)
    elif system == "Windows" and created:
        _set_windows_birthtime(path, created)


def _set_macos_birthtime(path: Path, dt: datetime) -> None:
    """Set macOS file creation (birth) time via setattrlist syscall."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

        # struct attrlist { u_short bitmapcount; u_short reserved; attrgroup_t commonattr; ... }
        # ATTR_BIT_MAP_COUNT = 5, ATTR_CMN_CRTIME = 0x00000200
        attrlist_buf = struct.pack("HHiiii", 5, 0, 0x00000200, 0, 0, 0)

        # struct timespec { time_t tv_sec; long tv_nsec; }
        ts_buf = struct.pack("ll", int(dt.timestamp()), 0)

        libc.setattrlist(
            str(path).encode("utf-8"),
            ctypes.c_char_p(attrlist_buf),
            ctypes.c_char_p(ts_buf),
            ctypes.c_size_t(len(ts_buf)),
            ctypes.c_ulong(0),
        )
    except Exception:
        pass


def _set_windows_birthtime(path: Path, dt: datetime) -> None:
    """Set Windows file creation time via SetFileTime (kernel32)."""
    try:
        # Windows FILETIME: 100-nanosecond intervals since 1601-01-01
        EPOCH_DIFF = 116444736000000000  # offset between 1601 and 1970 in 100ns units
        filetime = int(dt.timestamp() * 10_000_000) + EPOCH_DIFF

        kernel32 = ctypes.windll.kernel32

        # Open file with GENERIC_WRITE, share all, no inherit, OPEN_EXISTING
        handle = kernel32.CreateFileW(
            str(path),
            0x40000000,   # GENERIC_WRITE
            0x00000007,   # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
            None,
            3,            # OPEN_EXISTING
            0x80,         # FILE_ATTRIBUTE_NORMAL
            None,
        )
        if handle == ctypes.wintypes.HANDLE(-1).value:
            return

        ft = ctypes.wintypes.FILETIME(filetime & 0xFFFFFFFF, filetime >> 32)
        kernel32.SetFileTime(handle, ctypes.byref(ft), None, None)
        kernel32.CloseHandle(handle)
    except Exception:
        pass


# ── folder layout ─────────────────────────────────────────────────────────────

def note_folder(output_dir: Path, note: Note) -> Path:
    parts = [output_dir]
    if note.stack:
        parts.append(note.stack)
    parts.append(note.notebook)
    folder = Path(*parts)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _safe_name(name: str, max_length: int = 200) -> str:
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip()[:max_length]


def _unique_path(path: Path) -> Path:
    """If path exists, append (2), (3), ... until unique."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 2
    while True:
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


# ── docx builders ─────────────────────────────────────────────────────────────

def _build_doc(
    title: str,
    plain_text: str,
    note: Note,
    attachments: list[Attachment],
    folder: Path,
) -> Document:
    """
    Build a python-docx Document.
    Images are embedded inline; PDFs are written as sibling files and referenced by name.
    Returns the Document object (caller saves it).
    """
    doc = Document()

    # Source URL header
    if note.source_url:
        _add_paragraph(doc, f"Source: {note.source_url}")
        doc.add_paragraph()

    # Body text — split on newlines to preserve paragraph structure
    if plain_text:
        for line in plain_text.splitlines():
            _add_paragraph(doc, line)

    # Attachments
    for i, att in enumerate(attachments, start=1):
        if att.mime in _EMBEDDABLE_IMAGE_MIME:
            doc.add_paragraph()
            doc.add_picture(io.BytesIO(att.data), width=Inches(5))
        else:
            # Write PDF (or other) as a sibling file; insert a reference line
            filename = attachment_drive_filename(title, i, att)
            sibling = _unique_path(folder / filename)
            sibling.write_bytes(att.data)
            _add_file_hyperlink(doc, f"[Attachment: {sibling.name}]", sibling.name)

    return doc


# ── public API ────────────────────────────────────────────────────────────────

def write_note(
    classified: ClassifiedNote,
    output_dir: Path,
    multi_attachment: str,  # "doc" | "files"
) -> list[Path]:
    """
    Write the note to disk. Returns list of paths created.
    """
    note = classified.note
    folder = note_folder(output_dir, note)
    safe_title = _safe_name(note.title)

    if classified.kind == NoteKind.ATTACHMENT_ONLY_SINGLE:
        att = note.attachments[0]
        ext = _IMAGE_EXT.get(att.mime, _ext_for_mime(att.mime))
        dest = _unique_path(folder / f"{safe_title}{ext}")
        dest.write_bytes(att.data)
        _set_timestamps(dest, note.created, note.updated)
        return [dest]

    elif classified.kind == NoteKind.ATTACHMENT_ONLY_MULTI:
        if multi_attachment == "files":
            paths = []
            for i, att in enumerate(note.attachments, start=1):
                filename = attachment_drive_filename(safe_title, i, att)
                dest = _unique_path(folder / filename)
                dest.write_bytes(att.data)
                _set_timestamps(dest, note.created, note.updated)
                paths.append(dest)
            return paths
        else:  # doc
            doc = _build_doc(note.title, "", note, note.attachments, folder)
            dest = _unique_path(folder / f"{safe_title}.docx")
            doc.save(str(dest))
            _set_timestamps(dest, note.created, note.updated)
            return [dest]

    elif classified.kind == NoteKind.TEXT_ONLY:
        doc = _build_doc(note.title, classified.plain_text, note, [], folder)
        dest = _unique_path(folder / f"{safe_title}.docx")
        doc.save(str(dest))
        _set_timestamps(dest, note.created, note.updated)
        return [dest]

    elif classified.kind == NoteKind.TEXT_WITH_ATTACHMENTS:
        doc = _build_doc(note.title, classified.plain_text, note, note.attachments, folder)
        dest = _unique_path(folder / f"{safe_title}.docx")
        doc.save(str(dest))
        _set_timestamps(dest, note.created, note.updated)
        return [dest]

    raise ValueError(f"Unhandled note kind: {classified.kind}")


def _ext_for_mime(mime: str) -> str:
    mapping = {
        "application/pdf": ".pdf",
        "image/webp": ".webp",
    }
    return mapping.get(mime.lower(), "")

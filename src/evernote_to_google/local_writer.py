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
import os
import platform
import struct
from datetime import datetime
from pathlib import Path

from ._docx_builder import build_doc, add_file_hyperlink
from .classifier import (
    NoteKind, attachment_drive_filename, ClassifiedNote,
    _EMBEDDABLE_IMAGE_MIME, _safe_name, _ext_for_mime,
)
from .parser import Attachment, Note


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


# ── docx helpers ──────────────────────────────────────────────────────────────

def _write_sibling_files(doc, attachments: list[Attachment], title: str, folder: Path, note: Note) -> None:
    """Write non-image attachments as sibling files and add hyperlinks into doc."""
    sibling_index = 1
    for att in attachments:
        if att.mime not in _EMBEDDABLE_IMAGE_MIME:
            filename = attachment_drive_filename(title, sibling_index, att)
            sibling = _unique_path(folder / filename)
            sibling.write_bytes(att.data)
            _set_timestamps(sibling, note.created, note.updated)
            add_file_hyperlink(doc, f"[Attachment: {sibling.name}]", sibling.name)
            sibling_index += 1


def _save_doc(doc, path: Path, note: Note) -> Path:
    """Save doc to a unique path and set timestamps. Returns the path written."""
    dest = _unique_path(path)
    doc.save(str(dest))
    _set_timestamps(dest, note.created, note.updated)
    return dest


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
    attachments = classified.attachments

    if classified.kind == NoteKind.ATTACHMENT_ONLY_SINGLE:
        att = attachments[0]
        ext = _ext_for_mime(att.mime)
        dest = _unique_path(folder / f"{safe_title}{ext}")
        dest.write_bytes(att.data)
        _set_timestamps(dest, note.created, note.updated)
        return [dest]

    elif classified.kind == NoteKind.ATTACHMENT_ONLY_MULTI:
        if multi_attachment == "files":
            paths = []
            for i, att in enumerate(attachments, start=1):
                filename = attachment_drive_filename(safe_title, i, att)
                dest = _unique_path(folder / filename)
                dest.write_bytes(att.data)
                _set_timestamps(dest, note.created, note.updated)
                paths.append(dest)
            return paths
        else:  # doc
            doc = build_doc(note, attachments)
            _write_sibling_files(doc, attachments, note.title, folder, note)
            has_siblings = any(att.mime not in _EMBEDDABLE_IMAGE_MIME for att in attachments)
            docx_name = f"{safe_title}_0.docx" if has_siblings else f"{safe_title}.docx"
            return [_save_doc(doc, folder / docx_name, note)]

    elif classified.kind == NoteKind.TEXT_ONLY:
        doc = build_doc(note, [])
        return [_save_doc(doc, folder / f"{safe_title}.docx", note)]

    elif classified.kind == NoteKind.TEXT_WITH_ATTACHMENTS:
        doc = build_doc(note, attachments)
        _write_sibling_files(doc, attachments, note.title, folder, note)
        has_siblings = any(att.mime not in _EMBEDDABLE_IMAGE_MIME for att in attachments)
        docx_name = f"{safe_title}_0.docx" if has_siblings else f"{safe_title}.docx"
        return [_save_doc(doc, folder / docx_name, note)]

    raise ValueError(f"Unhandled note kind: {classified.kind}")

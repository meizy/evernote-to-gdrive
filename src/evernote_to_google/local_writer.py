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
    attachment_drive_filename, ClassifiedNote,
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

class LocalWriter:
    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    def note_exists(self, note: Note, safe_title: str) -> bool:
        folder = note_folder(self._output_dir, note)
        return any(folder.glob(f"{safe_title}.*")) or any(folder.glob(f"{safe_title}_0.*"))

    def write_doc(self, title: str, plain_text: str, attachments: list[Attachment], note: Note) -> str:
        folder = note_folder(self._output_dir, note)
        doc = build_doc(note, attachments)
        if attachments:
            _write_sibling_files(doc, attachments, note.title, folder, note)
        dest = _save_doc(doc, folder / f"{title}.docx", note)
        return str(dest)

    def write_raw_file(self, name: str, data: bytes, mime_type: str, note: Note) -> str:
        folder = note_folder(self._output_dir, note)
        ext = _ext_for_mime(mime_type)
        # attachment_drive_filename already includes the extension; single-attachment name doesn't
        if ext and not name.endswith(ext):
            name = f"{name}{ext}"
        dest = _unique_path(folder / name)
        dest.write_bytes(data)
        _set_timestamps(dest, note.created, note.updated)
        return str(dest)

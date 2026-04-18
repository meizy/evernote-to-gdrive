"""
Parse Evernote .enex export files into structured Note objects.

ENEX is an XML format. Each <note> contains:
  - <title>
  - <created> / <updated>  (YYYYMMDDTHHmmssZ)
  - <note-attributes> with optional <source-url>
  - <content>  a CDATA block containing ENML (Evernote Markup Language, an XHTML subset)
  - <resource> (0..n) each with <data> (base64), <mime>, <resource-attributes>/<file-name>
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from lxml import etree


@dataclass
class Attachment:
    mime: str
    data: bytes
    filename: str | None  # original filename from <resource-attributes>/<file-name>
    hash: str = ""       # MD5 hex digest of data (matches <en-media hash="..."> in ENML)


@dataclass
class Note:
    title: str
    notebook: str          # derived from the source .enex filename (stem)
    stack: str | None      # Evernote stack (subdirectory name), or None
    created: datetime | None
    updated: datetime | None
    source_url: str | None
    # Raw ENML content string (the <content> CDATA). Empty string if absent.
    enml: str
    tags: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)

    @property
    def modified_time(self) -> datetime | None:
        """Best-available modification time: updated if set, otherwise created."""
        return self.updated or self.created


@dataclass
class NotebookInfo:
    """Lightweight metadata derived from an enex file's path — no XML parsing needed."""
    notebook: str
    stack: str | None
    path: Path


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_date(text: str | None) -> datetime | None:
    """Parse Evernote date string '20231015T143000Z' → aware datetime."""
    if not text:
        return None
    text = text.strip()
    try:
        return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _text(element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip() or None


def _parse_resource(resource_el: etree._Element) -> Attachment:
    mime = _text(resource_el, "mime") or "application/octet-stream"

    data_el = resource_el.find("data")
    raw = b""
    if data_el is not None and data_el.text:
        raw = base64.b64decode(data_el.text.strip())

    filename: str | None = None
    attrs = resource_el.find("resource-attributes")
    if attrs is not None:
        filename = _text(attrs, "file-name")

    return Attachment(mime=mime, data=raw, filename=filename, hash=hashlib.md5(raw).hexdigest())


def _parse_note(note_el: etree._Element, notebook: str) -> Note:
    title = _text(note_el, "title") or "Untitled"
    created = _parse_date(_text(note_el, "created"))
    updated = _parse_date(_text(note_el, "updated"))

    source_url: str | None = None
    note_attrs = note_el.find("note-attributes")
    if note_attrs is not None:
        source_url = _text(note_attrs, "source-url")

    content_el = note_el.find("content")
    enml = ""
    if content_el is not None and content_el.text:
        enml = content_el.text.strip()

    tags = [t.text.strip() for t in note_el.findall("tag") if t.text]
    attachments = [_parse_resource(r) for r in note_el.findall("resource")]

    return Note(
        title=title,
        notebook=notebook,
        stack=None,
        created=created,
        updated=updated,
        source_url=source_url,
        enml=enml,
        tags=tags,
        attachments=attachments,
    )


# ── public API ────────────────────────────────────────────────────────────────

def parse_enex(path: Path, stack: str | None = None) -> Iterator[Note]:
    """Yield Note objects from a single .enex file, one at a time (streaming)."""
    notebook = path.stem
    for _event, note_el in etree.iterparse(str(path), events=("end",), tag="note"):
        note = _parse_note(note_el, notebook)
        note.stack = stack
        note_el.clear()
        parent = note_el.getparent()
        if parent is not None:
            parent.remove(note_el)
        yield note


def scan_enex_structure(input_path: Path) -> list[NotebookInfo]:
    """Return notebook/stack metadata from the filesystem without parsing any XML.

    Directory structure maps to Evernote stacks:
      <dir>/<notebook>.enex          → notebook, no stack
      <dir>/<stack>/<notebook>.enex  → notebook inside a stack
    """
    if not input_path.is_dir():
        if input_path.suffix.lower() != ".enex":
            raise ValueError(f"Expected a .enex file or directory, got: {input_path}")
        return [NotebookInfo(notebook=input_path.stem, stack=None, path=input_path)]

    enex_files = sorted(input_path.rglob("*.enex"))
    if not enex_files:
        raise ValueError(f"No .enex files found in {input_path}")

    result = []
    for enex_path in enex_files:
        relative = enex_path.relative_to(input_path)
        parts = relative.parts
        stack = parts[0] if len(parts) == 2 else None
        result.append(NotebookInfo(notebook=enex_path.stem, stack=stack, path=enex_path))
    return result


def count_notes(infos: list[NotebookInfo]) -> int:
    """Count notes across the given enex files without decoding any content."""
    total = 0
    for info in infos:
        for _event, elem in etree.iterparse(str(info.path), events=("end",), tag="note"):
            total += 1
            elem.clear()
            parent = elem.getparent()
            if parent is not None:
                parent.remove(elem)
    return total


def load_notes(input_path: Path) -> Iterator[Note]:
    """Accept either a single .enex file or a directory of .enex files."""
    for info in scan_enex_structure(input_path):
        yield from parse_enex(info.path, stack=info.stack)

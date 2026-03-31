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
    attachments: list[Attachment] = field(default_factory=list)


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

    attachments = [_parse_resource(r) for r in note_el.findall("resource")]

    return Note(
        title=title,
        notebook=notebook,
        stack=None,
        created=created,
        updated=updated,
        source_url=source_url,
        enml=enml,
        attachments=attachments,
    )


# ── public API ────────────────────────────────────────────────────────────────

def parse_enex(path: Path, stack: str | None = None) -> Iterator[Note]:
    """Yield Note objects from a single .enex file."""
    notebook = path.stem
    tree = etree.parse(str(path))
    root = tree.getroot()
    for note_el in root.findall("note"):
        note = _parse_note(note_el, notebook)
        note.stack = stack
        yield note


def parse_enex_dir(directory: Path) -> Iterator[Note]:
    """Yield Note objects from all .enex files in a directory (recursive).

    Directory structure maps to Evernote stacks:
      <dir>/<notebook>.enex          → notebook, no stack
      <dir>/<stack>/<notebook>.enex  → notebook inside a stack
    """
    enex_files = sorted(directory.rglob("*.enex"))
    if not enex_files:
        raise ValueError(f"No .enex files found in {directory}")
    for enex_path in enex_files:
        yield from _parse_enex_with_stack(enex_path, directory)


def _parse_enex_with_stack(path: Path, root: Path) -> Iterator[Note]:
    """Parse a single .enex file, injecting stack info derived from its path."""
    relative = path.relative_to(root)
    parts = relative.parts  # e.g. ('Startups', 'Funding.enex') or ('Seculert.enex',)
    notebook = path.stem
    stack = parts[0] if len(parts) == 2 else None  # only set for stack/notebook.enex

    tree = etree.parse(str(path))
    root_el = tree.getroot()
    for note_el in root_el.findall("note"):
        note = _parse_note(note_el, notebook)
        note.stack = stack
        yield note


def load_notes(input_path: Path) -> Iterator[Note]:
    """Accept either a single .enex file or a directory of .enex files."""
    if input_path.is_dir():
        yield from parse_enex_dir(input_path)
    elif input_path.suffix.lower() == ".enex":
        yield from parse_enex(input_path)
    else:
        raise ValueError(f"Expected a .enex file or directory, got: {input_path}")

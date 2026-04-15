"""
Shared dataclasses and enums for migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .parser import Attachment, Note


class AttachmentPolicy(str, Enum):
    DOC = "doc"
    FILES = "files"


class OutputMode(str, Enum):
    GOOGLE = "gdrive"
    LOCAL = "local"


class WebClipMode(str, Enum):
    PDF = "pdf"
    DOC = "doc"


class ClipTheme(str, Enum):
    LIGHT = "light"
    DARK = "dark"


class GDriveModifiedSource(str, Enum):
    CREATED = "created"
    UPDATED = "updated"


class MigrationStatus(str, Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class MigrationRecord:
    notebook: str
    title: str
    kind: str
    status: MigrationStatus
    output: list[str]   # Drive file IDs (api) or local paths (local)
    error: str = ""
    is_doc: bool = True  # False when output[0] is a raw Drive file ID (not a Google Doc)
    duration_s: float = 0.0
    output_name: str = ""
    embedded_images: int = 0
    sibling_files: int = 0


class WriterProtocol(Protocol):
    def note_exists(self, note: Note, safe_title_override: str | None = None) -> bool: ...
    def cleanup_note_files(self, safe_title: str, note: Note) -> None: ...

    def write_doc(self, title: str, attachments: list[Attachment], note: Note, **kwargs) -> str: ...
    def write_html_doc(self, title: str, html: str, note: Note) -> str: ...
    def write_raw_file(self, name: str, data: bytes, mime_type: str, note: Note) -> str: ...


@dataclass
class MigrationOptions:
    output_mode: OutputMode
    dest: str          # Drive folder path (gdrive), local output dir (local), or "null"
    notebooks: list[str]          # empty = all
    stacks: list[str]             # empty = all
    note: str | None              # if set, only migrate this one note title
    attachments: AttachmentPolicy
    log_file: Path | None
    secrets_folder: Path | None = None
    include_tags: bool = True
    verbose: bool = False
    skip_note_links: bool = False
    web_clip: WebClipMode = WebClipMode.PDF
    clip_theme: ClipTheme = ClipTheme.LIGHT
    force: bool = False
    gdrive_modified: GDriveModifiedSource = GDriveModifiedSource.CREATED

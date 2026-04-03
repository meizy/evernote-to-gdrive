"""
Shared dataclasses and enums for migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class AttachmentPolicy(str, Enum):
    DOC = "doc"
    FILES = "files"
    BOTH = "both"


class OutputMode(str, Enum):
    GOOGLE = "gdrive"
    LOCAL = "local"


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


@dataclass
class MigrationOptions:
    output_mode: OutputMode
    dest: str          # Drive folder path (gdrive), local output dir (local), or "null"
    dry_run: bool
    notebooks: list[str]          # empty = all
    stacks: list[str]             # empty = all
    note: str | None              # if set, only migrate this one note title
    attachments: AttachmentPolicy
    log_file: Path | None
    include_tags: bool = True
    verbose: bool = False

"""
Analyze .enex files and collect statistics without uploading anything.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from .classifier import NoteKind, classify
from .parser import Note


@dataclass
class AttachmentStats:
    count: int = 0
    total_bytes: int = 0
    by_mime: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    largest_bytes: int = 0
    largest_name: str = ""


@dataclass
class AnalysisResult:
    total_notes: int = 0
    by_notebook: dict[tuple[str | None, str], int] = field(default_factory=lambda: defaultdict(int))
    stacks: set = field(default_factory=set)

    # classification counts
    text_only: int = 0
    attachment_only_single: int = 0
    attachment_only_multi: int = 0
    text_with_attachments: int = 0

    # attachment info
    attachments: AttachmentStats = field(default_factory=AttachmentStats)
    notes_with_multi_attachments: int = 0

    # per-notebook attachment sizes
    attachment_bytes_by_notebook: dict[tuple[str | None, str], int] = field(default_factory=lambda: defaultdict(int))

    # issues
    empty_notes: int = 0       # no text AND no attachments
    encrypted_notes: int = 0   # ENML contains <en-crypt> tags


def run_analysis(notes: Iterable[Note]) -> AnalysisResult:
    result = AnalysisResult()

    for note in notes:
        result.total_notes += 1
        result.by_notebook[(note.stack, note.notebook)] += 1
        if note.stack:
            result.stacks.add(note.stack)

        classified = classify(note)

        match classified.kind:
            case NoteKind.TEXT_ONLY:
                result.text_only += 1
            case NoteKind.ATTACHMENT_ONLY_SINGLE:
                result.attachment_only_single += 1
            case NoteKind.ATTACHMENT_ONLY_MULTI:
                result.attachment_only_multi += 1
            case NoteKind.TEXT_WITH_ATTACHMENTS:
                result.text_with_attachments += 1

        if not classified.plain_text and not note.attachments:
            result.empty_notes += 1

        if "<en-crypt" in note.enml:
            result.encrypted_notes += 1

        if len(note.attachments) >= 2:
            result.notes_with_multi_attachments += 1

        for att in note.attachments:
            size = len(att.data)
            result.attachments.count += 1
            result.attachments.total_bytes += size
            result.attachments.by_mime[att.mime] += 1
            result.attachment_bytes_by_notebook[(note.stack, note.notebook)] += size
            if size > result.attachments.largest_bytes:
                result.attachments.largest_bytes = size
                result.attachments.largest_name = att.filename or note.title

    return result

"""
Analyze .enex files and report statistics without uploading anything.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .classifier import NoteKind, classify, _safe_name
from .display import rtl_display
from .parser import Note, load_notes

console = Console()


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
    by_notebook: dict[str, int] = field(default_factory=lambda: defaultdict(int))
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
    attachment_bytes_by_notebook: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # issues
    empty_notes: int = 0       # no text AND no attachments
    encrypted_notes: int = 0   # ENML contains <en-crypt> tags


def run_analysis(input_path: Path) -> AnalysisResult:
    result = AnalysisResult()

    for note in load_notes(input_path):
        result.total_notes += 1
        result.by_notebook[note.notebook] += 1
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

        n_att = len(note.attachments)
        if n_att >= 2:
            result.notes_with_multi_attachments += 1

        for att in note.attachments:
            size = len(att.data)
            result.attachments.count += 1
            result.attachments.total_bytes += size
            result.attachments.by_mime[att.mime] += 1
            result.attachment_bytes_by_notebook[note.notebook] += size
            if size > result.attachments.largest_bytes:
                result.attachments.largest_bytes = size
                result.attachments.largest_name = att.filename or note.title

    return result


def print_report(result: AnalysisResult) -> None:
    console.print()
    console.rule("[bold]Evernote Export Analysis")
    console.print()

    # Summary table
    summary = Table(show_header=False, box=None, padding=(0, 2))
    summary.add_column(style="bold cyan", no_wrap=True)
    summary.add_column()
    summary.add_row("Total notes", str(result.total_notes))
    summary.add_row("Notebooks", str(len(result.by_notebook)))
    if result.stacks:
        summary.add_row("Stacks", str(len(result.stacks)))
    console.print(summary)
    console.print()

    # Classification
    cls_table = Table(title="Note Classification", show_lines=False)
    cls_table.add_column("Type", style="bold")
    cls_table.add_column("Count", justify="right")
    cls_table.add_column("Output")
    cls_table.add_row(
        "Text only",
        str(result.text_only),
        "Document",
    )
    cls_table.add_row(
        "Attachment only (1 file)",
        str(result.attachment_only_single),
        "File",
    )
    cls_table.add_row(
        "Attachment only (multi)",
        str(result.attachment_only_multi),
        "Document (links) + files",
    )
    cls_table.add_row(
        "Text + attachment(s)",
        str(result.text_with_attachments),
        "Document + files",
    )
    console.print(cls_table)
    console.print()

    # Attachments
    if result.attachments.count:
        att_table = Table(title="Attachments")
        att_table.add_column("MIME type", style="bold")
        att_table.add_column("Count", justify="right")
        for mime, cnt in sorted(result.attachments.by_mime.items()):
            att_table.add_row(mime, str(cnt))
        console.print(att_table)
        console.print()

        total_mb = result.attachments.total_bytes / 1_048_576
        largest_mb = result.attachments.largest_bytes / 1_048_576
        att_summary = Table(show_header=False, box=None, padding=(0, 2))
        att_summary.add_column(style="bold cyan", no_wrap=True)
        att_summary.add_column()
        att_summary.add_row("Total attachments", str(result.attachments.count))
        att_summary.add_row("Total size", f"{total_mb:.1f} MB")
        att_summary.add_row(
            "Largest attachment",
            f"{largest_mb:.1f} MB  ({result.attachments.largest_name})",
        )
        att_summary.add_row(
            "Notes with multiple attachments",
            str(result.notes_with_multi_attachments),
        )
        console.print(att_summary)
        console.print()

    # Per-notebook
    nb_table = Table(title="Notes per Notebook")
    nb_table.add_column("Notebook", style="bold")
    nb_table.add_column("Notes", justify="right")
    for nb, cnt in sorted(result.by_notebook.items()):
        nb_table.add_row(rtl_display(nb), str(cnt))
    console.print(nb_table)
    console.print()

    # Top 10 notebooks by attachment size
    if result.attachment_bytes_by_notebook:
        top = sorted(result.attachment_bytes_by_notebook.items(), key=lambda x: x[1], reverse=True)[:10]
        top_table = Table(title="Top Notebooks by Attachment Size")
        top_table.add_column("Notebook", style="bold")
        top_table.add_column("Total Size", justify="right")
        for nb, nbytes in top:
            top_table.add_row(rtl_display(nb), f"{nbytes / 1_048_576:.1f} MB")
        console.print(top_table)
        console.print()

    # Warnings
    if result.empty_notes or result.encrypted_notes:
        console.rule("[yellow]Warnings")
        if result.empty_notes:
            console.print(f"[yellow]  {result.empty_notes} empty note(s) (no text, no attachments)")
        if result.encrypted_notes:
            console.print(
                f"[yellow]  {result.encrypted_notes} note(s) contain encrypted sections "
                "(encrypted blocks will be stripped from the output)"
            )
        console.print()


def list_notes_by_mime(input_path: Path, mime_type: str) -> None:
    matches: list[tuple[str, str, list[str]]] = []  # (notebook, title, filenames)
    for note in load_notes(input_path):
        matched = [att.filename or "<unnamed>" for att in note.attachments if att.mime == mime_type]
        if matched:
            matches.append((note.notebook, note.title, matched))

    if not matches:
        console.print(f"[yellow]No notes found with MIME type: {mime_type}")
        return

    table = Table(title=f"Notes with attachment type: {mime_type}")
    table.add_column("Notebook", style="bold")
    table.add_column("Note title")
    table.add_column("Matching files")
    for notebook, title, filenames in sorted(matches):
        table.add_row(rtl_display(notebook), title, ", ".join(filenames))
    console.print()
    console.print(table)
    console.print(f"\n[dim]{len(matches)} note(s) matched.")


def find_note(input_path: Path, title: str) -> None:
    matches: list[tuple[str, str | None]] = []  # (notebook, stack)
    for note in load_notes(input_path):
        if note.title == title:
            matches.append((note.notebook, note.stack))

    if not matches:
        console.print(f"[yellow]No note found with title: {title!r}")
        return

    table = Table(title=f"Note: {title!r}")
    table.add_column("Notebook", style="bold")
    table.add_column("Stack")
    for notebook, stack in sorted(matches):
        table.add_row(rtl_display(notebook), stack or "")
    console.print()
    console.print(table)
    console.print(f"\n[dim]{len(matches)} match(es).")


def report_duplicates(input_path: Path) -> None:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for note in load_notes(input_path):
        key = (note.notebook, _safe_name(note.title))
        groups[key].append(note.title)

    dups = {k: v for k, v in groups.items() if len(v) > 1}

    if not dups:
        console.print("[green]No duplicate note titles found.")
        return

    table = Table(title="Duplicate Notes (same notebook + same safe title)")
    table.add_column("Notebook", style="bold")
    table.add_column("Safe Title")
    table.add_column("Count", justify="right")
    table.add_column("Original Titles")
    for (notebook, safe_title), titles in sorted(dups.items()):
        table.add_row(
            rtl_display(notebook),
            rtl_display(safe_title),
            str(len(titles)),
            "\n".join(rtl_display(t) for t in dict.fromkeys(titles)),
        )
    console.print()
    console.print(table)
    total = sum(len(v) - 1 for v in dups.values())
    console.print(f"\n[yellow]{len(dups)} group(s) with duplicates — {total} note(s) will be renamed during migration (local: ' (2)' suffix; gdrive: same name kept).")


def report_tags(input_path: Path) -> None:
    tag_counts: dict[str, int] = defaultdict(int)
    for note in load_notes(input_path):
        for tag in note.tags:
            tag_counts[tag] += 1

    if not tag_counts:
        console.print("[yellow]No tags found.")
        return

    sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)

    table = Table(title="Tags by Note Count")
    table.add_column("Tag", style="bold")
    table.add_column("Notes", justify="right")
    for tag, count in sorted_tags:
        table.add_row(rtl_display(tag), str(count))
    console.print()
    console.print(table)
    console.print(f"\n[dim]{len(tag_counts)} unique tag(s).")


def save_json(result: AnalysisResult, path: Path) -> None:
    # Convert defaultdicts to plain dicts for JSON serialization
    data = {
        "total_notes": result.total_notes,
        "by_notebook": dict(result.by_notebook),
        "classification": {
            "text_only": result.text_only,
            "attachment_only_single": result.attachment_only_single,
            "attachment_only_multi": result.attachment_only_multi,
            "text_with_attachments": result.text_with_attachments,
        },
        "attachments": {
            "count": result.attachments.count,
            "total_bytes": result.attachments.total_bytes,
            "total_mb": round(result.attachments.total_bytes / 1_048_576, 2),
            "by_mime": dict(result.attachments.by_mime),
            "largest_bytes": result.attachments.largest_bytes,
            "largest_name": result.attachments.largest_name,
        },
        "notes_with_multi_attachments": result.notes_with_multi_attachments,
        "top_notebooks_by_attachment_size": dict(
            sorted(result.attachment_bytes_by_notebook.items(), key=lambda x: x[1], reverse=True)[:10]
        ),
        "warnings": {
            "empty_notes": result.empty_notes,
            "encrypted_notes": result.encrypted_notes,
        },
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

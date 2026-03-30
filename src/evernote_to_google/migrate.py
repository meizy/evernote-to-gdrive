"""
Migration orchestration: classify notes and dispatch to Drive/Docs or local folder.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Column

from .classifier import NoteKind, attachment_drive_filename, classify
from .parser import Note, load_notes

console = Console()


class MultiAttachmentPolicy(str, Enum):
    DOC = "doc"
    FILES = "files"


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
    dest: str          # Drive folder path (gdrive) or local output dir (local)
    dry_run: bool
    skip_existing: bool
    notebooks: list[str]          # empty = all
    stacks: list[str]             # empty = all
    multi_attachment: MultiAttachmentPolicy
    log_file: Path | None
    verbose: bool = False


def run_migration(input_path: Path, options: MigrationOptions, drive=None, docs=None) -> list[MigrationRecord]:
    notes = list(load_notes(input_path))

    if options.stacks:
        stack_set = set(options.stacks)
        available_stacks = {n.stack for n in notes if n.stack is not None}
        missing = stack_set - available_stacks
        if missing:
            console.print(f"[red]Error: stack(s) not found: {', '.join(sorted(missing))}[/]")
            if available_stacks:
                console.print(f"  Available stacks: {', '.join(sorted(available_stacks))}")
            return []
        notes = [n for n in notes if n.stack in stack_set]
    if options.notebooks:
        nb_set = set(options.notebooks)
        available_notebooks = {n.notebook for n in notes}
        missing = nb_set - available_notebooks
        if missing:
            console.print(f"[red]Error: notebook(s) not found: {', '.join(sorted(missing))}[/]")
            if available_notebooks:
                console.print(f"  Available notebooks: {', '.join(sorted(available_notebooks))}")
            return []
        notes = [n for n in notes if n.notebook in nb_set]

    if options.dry_run:
        _dry_run(notes, options, drive)
        return []

    records: list[MigrationRecord] = []
    folder_cache: dict = {}

    if options.verbose:
        for note in notes:
            record = _migrate_note(note=note, options=options, drive=drive, docs=docs, folder_cache=folder_cache)
            records.append(record)
            label = f"[cyan]{note.notebook}[/] / {note.title}"
            if record.status == MigrationStatus.SKIPPED:
                console.print(f"{label} - skipped")
            else:
                console.print(label)
    else:
        with Progress(
            TextColumn("[progress.description]{task.description}", table_column=Column(width=55, no_wrap=True)),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Migrating notes", total=len(notes))

            for note in notes:
                progress.update(task, description=f"[cyan]{note.notebook}[/] / {note.title[:40]}")
                record = _migrate_note(note=note, options=options, drive=drive, docs=docs, folder_cache=folder_cache)
                records.append(record)
                progress.advance(task)

    if options.log_file:
        _write_log(records, options.log_file)
    _print_summary(records)
    return records


# ── dry run ───────────────────────────────────────────────────────────────────

def _dry_run(notes: list[Note], options: MigrationOptions, drive) -> None:
    """Create only the migration root folder in Drive to validate auth/access."""
    from .drive import get_or_create_folder_path
    root_id = get_or_create_folder_path(drive, options.dest)
    console.print(f"  [green]Created root folder:[/] {options.dest} (id: {root_id})")
    console.print("\n[green]Dry run complete.[/]")


# ── per-note dispatch ─────────────────────────────────────────────────────────

def _migrate_note(note: Note, options: MigrationOptions, drive, docs, folder_cache: dict) -> MigrationRecord:
    classified = classify(note)
    kind_label = classified.kind.name.lower()

    try:
        if options.output_mode == OutputMode.GOOGLE:
            return _migrate_note_gdrive(note, classified, kind_label, options, drive, docs, folder_cache)
        else:
            from .local_writer import write_note, note_folder
            from .classifier import _safe_name
            if options.skip_existing:
                folder = note_folder(Path(options.dest), note)
                safe_title = _safe_name(note.title)
                if any(folder.glob(f"{safe_title}.*")) or any(folder.glob(f"{safe_title}_0.*")):
                    return MigrationRecord(
                        notebook=note.notebook, title=note.title, kind=kind_label,
                        status=MigrationStatus.SKIPPED, output=[],
                    )
            paths = write_note(classified, Path(options.dest), options.multi_attachment.value)
            return MigrationRecord(
                notebook=note.notebook, title=note.title, kind=kind_label,
                status=MigrationStatus.SUCCESS, output=[str(p) for p in paths],
            )

    except Exception as exc:
        console.print(f"[red]  Error: {note.title!r}: {exc}")
        return MigrationRecord(
            notebook=note.notebook, title=note.title, kind=kind_label,
            status=MigrationStatus.ERROR, output=[], error=str(exc),
        )


def _migrate_note_gdrive(note, classified, kind_label, options, drive, docs, folder_cache) -> MigrationRecord:
    from .classifier import _EMBEDDABLE_IMAGE_MIME
    from .docs import create_doc
    from .drive import ensure_folder_path, file_exists, make_description, upload_file

    cache_key = f"{note.stack or ''}/{note.notebook}"
    if cache_key not in folder_cache:
        folder_cache[cache_key] = ensure_folder_path(
            drive, options.dest, note.notebook, stack=note.stack
        )
    _, notebook_id = folder_cache[cache_key]

    description = make_description(note.created, note.source_url)

    if options.skip_existing and file_exists(drive, note.title, notebook_id):
        return MigrationRecord(
            notebook=note.notebook, title=note.title, kind=kind_label,
            status=MigrationStatus.SKIPPED, output=[],
        )

    kind = classified.kind
    plain_text = classified.plain_text
    modified_time = note.updated or note.created

    if kind == NoteKind.TEXT_ONLY:
        file_id = create_doc(drive, docs, title=note.title, plain_text=plain_text,
                             note=note, attachments=[], parent_id=notebook_id, description=description,
                             modified_time=modified_time)
        return MigrationRecord(notebook=note.notebook, title=note.title, kind=kind_label,
                               status=MigrationStatus.SUCCESS, output=[file_id])

    elif kind == NoteKind.ATTACHMENT_ONLY_SINGLE:
        att = note.attachments[0]
        file_id = upload_file(drive, name=note.title, data=att.data, mime_type=att.mime,
                              parent_id=notebook_id, description=description,
                              modified_time=modified_time)
        return MigrationRecord(notebook=note.notebook, title=note.title, kind=kind_label,
                               status=MigrationStatus.SUCCESS, output=[file_id])

    elif kind == NoteKind.ATTACHMENT_ONLY_MULTI:
        if options.multi_attachment == MultiAttachmentPolicy.FILES:
            ids = []
            for i, att in enumerate(note.attachments, start=1):
                fname = attachment_drive_filename(note.title, i, att)
                ids.append(upload_file(drive, name=fname, data=att.data, mime_type=att.mime,
                                       parent_id=notebook_id, description=description,
                                       modified_time=modified_time))
            return MigrationRecord(notebook=note.notebook, title=note.title, kind=kind_label,
                                   status=MigrationStatus.SUCCESS, output=ids)
        else:
            has_siblings = any(att.mime not in _EMBEDDABLE_IMAGE_MIME for att in note.attachments)
            doc_title = f"{note.title}_0" if has_siblings else note.title
            file_id = create_doc(drive, docs, title=doc_title, plain_text="",
                                 note=note, attachments=note.attachments, parent_id=notebook_id,
                                 description=description, modified_time=modified_time)
            return MigrationRecord(notebook=note.notebook, title=note.title, kind=kind_label,
                                   status=MigrationStatus.SUCCESS, output=[file_id])

    elif kind == NoteKind.TEXT_WITH_ATTACHMENTS:
        has_siblings = any(att.mime not in _EMBEDDABLE_IMAGE_MIME for att in note.attachments)
        doc_title = f"{note.title}_0" if has_siblings else note.title
        file_id = create_doc(drive, docs, title=doc_title, plain_text=plain_text,
                             note=note, attachments=note.attachments, parent_id=notebook_id,
                             description=description, modified_time=modified_time)
        return MigrationRecord(notebook=note.notebook, title=note.title, kind=kind_label,
                               status=MigrationStatus.SUCCESS, output=[file_id])

    raise ValueError(f"Unhandled note kind: {kind}")


# ── logging & summary ─────────────────────────────────────────────────────────

def _write_log(records: list[MigrationRecord], log_file: Path) -> None:
    with log_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["notebook", "title", "kind", "status", "output", "error"])
        for r in records:
            writer.writerow([r.notebook, r.title, r.kind, r.status.value, "|".join(r.output), r.error])


def _print_summary(records: list[MigrationRecord]) -> None:
    total = len(records)
    success = sum(1 for r in records if r.status == MigrationStatus.SUCCESS)
    skipped = sum(1 for r in records if r.status == MigrationStatus.SKIPPED)
    errors = sum(1 for r in records if r.status == MigrationStatus.ERROR)

    console.print()
    console.rule("[bold]Migration Summary")
    console.print(f"  Total:   {total}")
    console.print(f"  [green]Success: {success}")
    if skipped:
        console.print(f"  [yellow]Skipped: {skipped}")
    if errors:
        console.print(f"  [red]Errors:  {errors}")
        for r in records:
            if r.status == MigrationStatus.ERROR:
                console.print(f"  [red]- {r.notebook}/{r.title}: {r.error}")
    console.print()

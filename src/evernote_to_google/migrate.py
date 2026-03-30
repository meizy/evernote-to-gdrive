"""
Migration orchestration: classify notes and dispatch to Drive/Docs or local folder.
"""

from __future__ import annotations

import csv
import shutil
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Column

from .classifier import NoteKind, attachment_drive_filename, classify
from .parser import Note, load_notes

console = Console()


def _eprint(*args, **kwargs):
    # Use stderr directly instead of Rich's console.print for errors and warnings.
    # Rich is a third-party library whose markup parser can silently swallow brackets
    # in exception messages (e.g. "[Errno 2]"), and its Progress display can visually
    # bury output written during a live render. Stderr is always reliable.
    print(*args, file=sys.stderr, flush=True, **kwargs)


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
    dest: str          # Drive folder path (gdrive), local output dir (local), or "null"
    dry_run: bool
    skip_existing: bool
    notebooks: list[str]          # empty = all
    stacks: list[str]             # empty = all
    note: str | None              # if set, only migrate this one note title
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
            _eprint(f"Error: stack(s) not found: {', '.join(sorted(missing))}")
            if available_stacks:
                _eprint(f"  Available stacks: {', '.join(sorted(available_stacks))}")
            return []
        notes = [n for n in notes if n.stack in stack_set]
    if options.notebooks:
        nb_set = set(options.notebooks)
        available_notebooks = {n.notebook for n in notes}
        missing = nb_set - available_notebooks
        if missing:
            _eprint(f"Error: notebook(s) not found: {', '.join(sorted(missing))}")
            if available_notebooks:
                _eprint(f"  Available notebooks: {', '.join(sorted(available_notebooks))}")
            return []
        notes = [n for n in notes if n.notebook in nb_set]

    if options.note:
        notes = [n for n in notes if n.title == options.note]
        if not notes:
            _eprint(f"Error: note {options.note!r} not found in the selected notebook(s).")
            return []

    if options.dry_run:
        _dry_run(notes, options, drive)
        return []

    records: list[MigrationRecord] = []
    folder_cache: dict = {}

    # In null mode create one shared temp dir for the whole run instead of
    # one per note; _migrate_note_local writes there and we clean up at the end.
    null_tmp: Path | None = None
    if options.output_mode == OutputMode.LOCAL and options.dest == "null":
        null_tmp = Path(tempfile.mkdtemp())
        options = MigrationOptions(**{**options.__dict__, "dest": str(null_tmp)})

    try:
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
    finally:
        if null_tmp is not None:
            shutil.rmtree(null_tmp, ignore_errors=True)

    if options.log_file:
        _write_log(records, options.log_file)
    _print_summary(records)
    return records


# ── dry run ───────────────────────────────────────────────────────────────────

def _dry_run(notes: list[Note], options: MigrationOptions, drive) -> None:
    """Create only the migration root folder in Drive to validate auth/access."""
    from .drive import get_or_create_folder_path
    root_id = get_or_create_folder_path(drive, options.dest)
    console.print(f"  [green]Created root folder:[/] '{options.dest}' (id: {root_id})")
    console.print("\n[green]Dry run complete.[/]")


# ── per-note dispatch ─────────────────────────────────────────────────────────

def _migrate_note(note: Note, options: MigrationOptions, drive, docs, folder_cache: dict) -> MigrationRecord:
    classified = classify(note)
    kind_label = classified.kind.name.lower()

    try:
        if options.output_mode == OutputMode.GOOGLE:
            return _migrate_note_gdrive(note, classified, kind_label, options, drive, docs, folder_cache)
        else:
            return _migrate_note_local(note, classified, kind_label, options)

    except Exception as exc:
        _eprint(f"Error: {note.title!r}: {exc} ({type(exc).__name__})")
        return MigrationRecord(
            notebook=note.notebook, title=note.title, kind=kind_label,
            status=MigrationStatus.ERROR, output=[], error=str(exc),
        )


def _migrate_note_local(note, classified, kind_label, options) -> MigrationRecord:
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


def _migrate_note_gdrive(note, classified, kind_label, options, drive, docs, folder_cache) -> MigrationRecord:
    from .classifier import _EMBEDDABLE_IMAGE_MIME, _safe_name
    from .docs import create_doc
    from .drive import ensure_folder_path, file_exists, make_description, upload_file

    cache_key = f"{note.stack or ''}/{note.notebook}"
    if cache_key not in folder_cache:
        folder_cache[cache_key] = ensure_folder_path(
            drive, options.dest, note.notebook, stack=note.stack
        )
    _, notebook_id = folder_cache[cache_key]

    description = make_description(note.created, note.source_url)
    safe_title = _safe_name(note.title)
    attachments = classified.attachments

    if options.skip_existing and file_exists(drive, safe_title, notebook_id):
        return MigrationRecord(
            notebook=note.notebook, title=note.title, kind=kind_label,
            status=MigrationStatus.SKIPPED, output=[],
        )

    kind = classified.kind
    plain_text = classified.plain_text
    modified_time = note.updated or note.created

    if kind == NoteKind.TEXT_ONLY:
        file_id = create_doc(drive, docs, title=safe_title, plain_text=plain_text,
                             note=note, attachments=[], parent_id=notebook_id, description=description,
                             modified_time=modified_time)
        return MigrationRecord(notebook=note.notebook, title=note.title, kind=kind_label,
                               status=MigrationStatus.SUCCESS, output=[file_id])

    elif kind == NoteKind.ATTACHMENT_ONLY_SINGLE:
        att = attachments[0]
        file_id = upload_file(drive, name=safe_title, data=att.data, mime_type=att.mime,
                              parent_id=notebook_id, description=description,
                              modified_time=modified_time)
        return MigrationRecord(notebook=note.notebook, title=note.title, kind=kind_label,
                               status=MigrationStatus.SUCCESS, output=[file_id])

    elif kind == NoteKind.ATTACHMENT_ONLY_MULTI:
        if options.multi_attachment == MultiAttachmentPolicy.FILES:
            ids = []
            for i, att in enumerate(attachments, start=1):
                fname = attachment_drive_filename(note.title, i, att)
                ids.append(upload_file(drive, name=fname, data=att.data, mime_type=att.mime,
                                       parent_id=notebook_id, description=description,
                                       modified_time=modified_time))
            return MigrationRecord(notebook=note.notebook, title=note.title, kind=kind_label,
                                   status=MigrationStatus.SUCCESS, output=ids)
        else:
            has_siblings = any(att.mime not in _EMBEDDABLE_IMAGE_MIME for att in attachments)
            doc_title = f"{safe_title}_0" if has_siblings else safe_title
            file_id = create_doc(drive, docs, title=doc_title, plain_text="",
                                 note=note, attachments=attachments, parent_id=notebook_id,
                                 description=description, modified_time=modified_time)
            return MigrationRecord(notebook=note.notebook, title=note.title, kind=kind_label,
                                   status=MigrationStatus.SUCCESS, output=[file_id])

    elif kind == NoteKind.TEXT_WITH_ATTACHMENTS:
        has_siblings = any(att.mime not in _EMBEDDABLE_IMAGE_MIME for att in attachments)
        doc_title = f"{safe_title}_0" if has_siblings else safe_title
        file_id = create_doc(drive, docs, title=doc_title, plain_text=plain_text,
                             note=note, attachments=attachments, parent_id=notebook_id,
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
        console.print(f"  [red]Errors:  {errors}[/]")
        for r in records:
            if r.status == MigrationStatus.ERROR:
                _eprint(f"  - {r.notebook}/{r.title}: {r.error}")
    console.print()

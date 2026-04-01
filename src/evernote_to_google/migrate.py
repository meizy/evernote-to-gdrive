"""
Migration orchestration: classify notes and dispatch to Drive/Docs or local folder.
"""

from __future__ import annotations

import csv
import itertools
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Column

from .classifier import NoteKind, attachment_label, attachment_sibling_filename, classify, _safe_name, _EMBEDDABLE_IMAGE_MIME
from .parser import Note, load_notes

console = Console()


def _eprint(*args, **kwargs):
    # Use stderr directly instead of Rich's console.print for errors and warnings.
    # Rich is a third-party library whose markup parser can silently swallow brackets
    # in exception messages (e.g. "[Errno 2]"), and its Progress display can visually
    # bury output written during a live render. Stderr is always reliable.
    print(*args, file=sys.stderr, flush=True, **kwargs)


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
    verbose: bool = False


def run_migration(input_path: Path, options: MigrationOptions) -> list[MigrationRecord]:
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

    # In null mode create one shared temp dir, deleted at the end
    null_tmp: Path | None = None
    if options.output_mode == OutputMode.LOCAL and options.dest == "null":
        null_tmp = Path(tempfile.mkdtemp())
        options = MigrationOptions(**{**options.__dict__, "dest": str(null_tmp)})

    if options.output_mode == OutputMode.GOOGLE:
        from .gdrive_writer import GDriveWriter
        writer = GDriveWriter(options.dest, options.attachments)
    else:
        from .local_writer import LocalWriter
        writer = LocalWriter(Path(options.dest), options.attachments)

    if options.dry_run:
        root_id = writer.dry_run()
        console.print(f"  [green]Created root folder:[/] '{options.dest}' (id: {root_id})")
        console.print("\n[green]Dry run complete.[/]")
        return []

    records: list[MigrationRecord] = []

    try:
        if options.verbose:
            gdrive = options.output_mode == OutputMode.GOOGLE
            for notebook, group in itertools.groupby(notes, key=lambda n: n.notebook):
                nb_start = time.monotonic()
                nb_count = 0
                for note in group:
                    t0 = time.monotonic()
                    record = _migrate_note(note=note, options=options, writer=writer)
                    elapsed = time.monotonic() - t0
                    records.append(record)
                    nb_count += 1
                    label = f"[cyan]{note.notebook}[/] / {note.title}"
                    if record.status == MigrationStatus.SKIPPED:
                        console.print(f"{label} - skipped")
                    elif gdrive:
                        console.print(f"{label} ({elapsed:.1f}s)")
                    else:
                        console.print(label)
                if gdrive:
                    nb_elapsed = time.monotonic() - nb_start
                    console.print(f"  [dim]{notebook}: {nb_elapsed:.1f}s total ({nb_count} notes)")
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
                    record = _migrate_note(note=note, options=options, writer=writer)
                    records.append(record)
                    progress.advance(task)
    finally:
        if null_tmp is not None:
            shutil.rmtree(null_tmp, ignore_errors=True)

    if options.log_file:
        _write_log(records, options.log_file)
    _print_summary(records)
    return records


# ── per-note dispatch ─────────────────────────────────────────────────────────

def _has_doc_siblings(attachments: list, policy: AttachmentPolicy) -> bool:
    """Return True if the doc will have sibling files (determines whether _0 suffix is needed)."""
    if policy in (AttachmentPolicy.BOTH, AttachmentPolicy.FILES):
        return len(attachments) > 0
    # DOC: only non-embeddable attachments become siblings
    return any(a.mime not in _EMBEDDABLE_IMAGE_MIME for a in attachments)


def _migrate_note(note: Note, options: MigrationOptions, writer) -> MigrationRecord:
    classified = classify(note)
    kind_label = classified.kind.name.lower()
    safe_title = _safe_name(note.title)

    try:
        if writer.note_exists(note, safe_title):
            return MigrationRecord(
                notebook=note.notebook, title=note.title, kind=kind_label,
                status=MigrationStatus.SKIPPED, output=[],
            )

        kind = classified.kind
        attachments = classified.attachments
        plain_text = classified.plain_text

        # Web-clipped notes have a source_url. Force doc policy to avoid
        # producing many junk sibling files from page images.
        policy = AttachmentPolicy.DOC if note.source_url else options.attachments

        if kind == NoteKind.TEXT_ONLY:
            output = [writer.write_doc(safe_title, plain_text, [], note)]

        elif kind == NoteKind.ATTACHMENT_ONLY_SINGLE:
            att = attachments[0]
            output = [writer.write_raw_file(safe_title, att.data, att.mime, note)]

        elif kind == NoteKind.ATTACHMENT_ONLY_MULTI:
            if policy == AttachmentPolicy.FILES:
                counters: dict[str, int] = defaultdict(int)
                output = []
                for att in attachments:
                    label = attachment_label(att.mime)
                    counters[label] += 1
                    filename = attachment_sibling_filename(note.title, label, counters[label], att)
                    output.append(writer.write_raw_file(filename, att.data, att.mime, note))
            else:
                has_siblings = _has_doc_siblings(attachments, policy)
                doc_title = f"{safe_title}_0" if has_siblings else safe_title
                output = [writer.write_doc(doc_title, "", attachments, note, policy)]

        elif kind == NoteKind.TEXT_WITH_ATTACHMENTS:
            # FILES implies BOTH for text notes: the doc must exist for the text,
            # so all attachments are also written as siblings.
            effective = AttachmentPolicy.BOTH if policy == AttachmentPolicy.FILES else policy
            has_siblings = _has_doc_siblings(attachments, effective)
            doc_title = f"{safe_title}_0" if has_siblings else safe_title
            output = [writer.write_doc(doc_title, plain_text, attachments, note, effective)]

        else:
            raise ValueError(f"Unhandled note kind: {kind}")

        return MigrationRecord(
            notebook=note.notebook, title=note.title, kind=kind_label,
            status=MigrationStatus.SUCCESS, output=output,
        )

    except Exception as exc:
        _eprint(f"Error: {note.title!r}: {exc} ({type(exc).__name__})")
        return MigrationRecord(
            notebook=note.notebook, title=note.title, kind=kind_label,
            status=MigrationStatus.ERROR, output=[], error=str(exc),
        )


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

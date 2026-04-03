"""
Migration orchestration: classify notes and dispatch to Drive/Docs or local folder.
"""

from __future__ import annotations

import csv
import shutil
import sys
import tempfile
import time
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Column

from .dispatch import migrate_note
from .display import rtl_display
from .drive import get_bytes_uploaded, log_throttle_summary, reset_throttle_sleep_total
from .models import AttachmentPolicy, MigrationOptions, MigrationRecord, MigrationStatus, OutputMode
from .parser import NotebookInfo, count_notes, parse_enex, scan_enex_structure

console = Console()

# Re-export for callers that currently import these from migrate
__all__ = [
    "AttachmentPolicy", "OutputMode", "MigrationStatus",
    "MigrationRecord", "MigrationOptions", "run_migration",
]


def _eprint(*args, **kwargs):
    # Use stderr directly instead of Rich's console.print for errors and warnings.
    # Rich is a third-party library whose markup parser can silently swallow brackets
    # in exception messages (e.g. "[Errno 2]"), and its Progress display can visually
    # bury output written during a live render. Stderr is always reliable.
    print(*args, file=sys.stderr, flush=True, **kwargs)


def _apply_filters(structure: list[NotebookInfo], options: MigrationOptions) -> list[NotebookInfo] | None:
    """Validate stack/notebook filters against filesystem structure.

    Returns filtered list on success, or None if a filter references a missing stack/notebook.
    """
    filtered = structure

    if options.stacks:
        stack_set = set(options.stacks)
        available = {s.stack for s in structure if s.stack is not None}
        missing = stack_set - available
        if missing:
            _eprint(f"Error: stack(s) not found: {', '.join(sorted(missing))}")
            if available:
                _eprint(f"  Available stacks: {', '.join(sorted(available))}")
            return None
        filtered = [s for s in filtered if s.stack in stack_set]

    if options.notebooks:
        nb_set = set(options.notebooks)
        available_nb = {s.notebook for s in structure}
        missing_nb = nb_set - available_nb
        if missing_nb:
            _eprint(f"Error: notebook(s) not found: {', '.join(rtl_display(n) for n in sorted(missing_nb))}")
            if available_nb:
                _eprint(f"  Available notebooks: {', '.join(rtl_display(n) for n in sorted(available_nb))}")
            return None
        filtered = [s for s in filtered if s.notebook in nb_set]

    return filtered


def _run_verbose(filtered: list[NotebookInfo], options: MigrationOptions, writer, seen: dict, records: list) -> None:
    gdrive = options.output_mode == OutputMode.GOOGLE
    for info in filtered:
        nb_start = time.monotonic()
        nb_count = 0
        nb_bytes = 0
        reset_throttle_sleep_total()
        for note in parse_enex(info.path, stack=info.stack):
            if options.note and note.title != options.note:
                continue
            t0 = time.monotonic()
            nb_bytes += len(note.enml.encode("utf-8")) if note.enml else 0
            nb_bytes += sum(len(a.data) for a in note.attachments)
            record = migrate_note(note=note, options=options, writer=writer, seen=seen)
            elapsed = time.monotonic() - t0
            records.append(record)
            nb_count += 1
            label = f"[cyan]{rtl_display(note.notebook)}[/] / {rtl_display(note.title)}"
            if record.status == MigrationStatus.SKIPPED:
                console.print(f"{label} - skipped")
            elif gdrive:
                console.print(f"{label} ({elapsed:.1f}s)")
            else:
                console.print(label)
        nb_elapsed = time.monotonic() - nb_start
        nb_mb = nb_bytes / (1024 * 1024)
        console.print(f"  [dim]{rtl_display(info.notebook)}: {nb_count} notes, {nb_elapsed:.1f}s, {nb_mb:.1f}MB")
        if gdrive:
            log_throttle_summary(info.notebook, nb_elapsed)


def _run_progress(filtered: list[NotebookInfo], total: int, options: MigrationOptions, writer, seen: dict, records: list) -> None:
    with Progress(
        TextColumn("[progress.description]{task.description}", table_column=Column(width=55, no_wrap=True)),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Migrating notes", total=total)
        for info in filtered:
            for note in parse_enex(info.path, stack=info.stack):
                if options.note and note.title != options.note:
                    continue
                progress.update(task, description=f"[cyan]{note.notebook}[/] / {note.title[:40]}")
                record = migrate_note(note=note, options=options, writer=writer, seen=seen)
                records.append(record)
                progress.advance(task)


def run_migration(input_path: Path, options: MigrationOptions) -> list[MigrationRecord]:
    structure = scan_enex_structure(input_path)
    filtered = _apply_filters(structure, options)
    if filtered is None:
        return []

    # In null mode create one shared temp dir, deleted at the end
    null_tmp: Path | None = None
    if options.output_mode == OutputMode.LOCAL and options.dest == "null":
        null_tmp = Path(tempfile.mkdtemp())
        options = MigrationOptions(**{**options.__dict__, "dest": str(null_tmp)})

    if options.output_mode == OutputMode.GOOGLE:
        from .gdrive_writer import GDriveWriter
        writer = GDriveWriter(options.dest, options.attachments, include_tags=options.include_tags)
    else:
        from .local_writer import LocalWriter
        writer = LocalWriter(Path(options.dest), options.attachments, include_tags=options.include_tags)

    if options.dry_run:
        root_id = writer.dry_run()
        console.print(f"  [green]Created root folder:[/] '{options.dest}' (id: {root_id})")
        console.print("\n[green]Dry run complete.[/]")
        return []

    records: list[MigrationRecord] = []
    seen: dict[tuple[str, str], int] = {}

    try:
        if options.verbose:
            _run_verbose(filtered, options, writer, seen, records)
        else:
            total = count_notes(filtered)
            _run_progress(filtered, total, options, writer, seen, records)
    finally:
        if null_tmp is not None:
            shutil.rmtree(null_tmp, ignore_errors=True)

    if options.note and not records:
        _eprint(f"Error: note {rtl_display(options.note)!r} not found in the selected notebook(s).")
        return []

    if options.log_file:
        _write_log(records, options.log_file)

    seen_stacks = {info.stack for info in filtered if info.stack}
    seen_notebooks = {info.notebook for info in filtered}
    _print_summary(records, seen_stacks, seen_notebooks, is_gdrive=options.output_mode == OutputMode.GOOGLE)
    return records


# ── logging & summary ─────────────────────────────────────────────────────────

def _write_log(records: list[MigrationRecord], log_file: Path) -> None:
    with log_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["notebook", "title", "kind", "status", "output", "error"])
        for r in records:
            writer.writerow([r.notebook, r.title, r.kind, r.status.value, "|".join(r.output), r.error])


def _print_summary(records: list[MigrationRecord], seen_stacks: set[str], seen_notebooks: set[str], is_gdrive: bool) -> None:
    total = len(records)
    success = sum(1 for r in records if r.status == MigrationStatus.SUCCESS)
    skipped = sum(1 for r in records if r.status == MigrationStatus.SKIPPED)
    errors = sum(1 for r in records if r.status == MigrationStatus.ERROR)

    console.print()
    console.rule("[bold]Migration Summary")
    if seen_stacks:
        console.print(f"  Stacks:    {len(seen_stacks)}")
    console.print(f"  Notebooks: {len(seen_notebooks)}")
    console.print(f"  Notes:     {total}")
    console.print(f"  [green]Success:   {success}")
    if skipped:
        console.print(f"  [yellow]Skipped:   {skipped}")
    if errors:
        console.print(f"  [red]Errors:    {errors}[/]")
        for r in records:
            if r.status == MigrationStatus.ERROR:
                _eprint(f"  - {rtl_display(r.notebook)}/{rtl_display(r.title)}: {r.error}")
    if is_gdrive:
        mb = get_bytes_uploaded() / (1024 * 1024)
        console.print(f"  Uploaded:  ~{mb:.1f} MB (estimate)")
    console.print()

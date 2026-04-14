"""
Migration orchestration: classify notes and dispatch to Drive/Docs or local folder.
"""

from __future__ import annotations

import csv
import logging
import shutil
import tempfile
import time
from contextlib import nullcontext as _nullcontext
from pathlib import Path

from ._console import console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Column

from googleapiclient.errors import HttpError

from .dispatch import migrate_note
from .display import rtl_display
from .drive_retry import get_bytes_uploaded, log_throttle_summary, reset_throttle_sleep_total
from .interlinks import DeferredInterlinkNote
from .models import AttachmentPolicy, MigrationOptions, MigrationRecord, MigrationStatus, OutputMode
from .classifier import sanitize_name
from .parser import NotebookInfo, count_notes, parse_enex, scan_enex_structure

_log = logging.getLogger(__name__)


def _migration_progress() -> Progress:
    """Build a standard progress bar for migration passes."""
    return Progress(
        TextColumn("[progress.description]{task.description}", table_column=Column(width=55, no_wrap=True)),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    )

# Re-export for callers that currently import these from migrate
__all__ = [
    "AttachmentPolicy", "OutputMode", "MigrationStatus",
    "MigrationRecord", "MigrationOptions", "run_migration",
]


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
            _log.error("stack(s) not found: %s", ', '.join(sorted(missing)))
            if available:
                _log.error("  Available stacks: %s", ', '.join(sorted(available)))
            return None
        filtered = [s for s in filtered if s.stack in stack_set]

    if options.notebooks:
        # Notebook names on disk may have special chars (e.g. ":") replaced with "_"
        # by the OS. Sanitize user input the same way before matching.
        sanitized_to_original = {sanitize_name(n): n for n in options.notebooks}
        sanitized_nb_set = set(sanitized_to_original)
        available_nb = {s.notebook for s in structure}
        missing_nb = sanitized_nb_set - available_nb
        if missing_nb:
            original_missing = {sanitized_to_original[n] for n in missing_nb}
            _log.error("notebook(s) not found: %s", ', '.join(rtl_display(n) for n in sorted(original_missing)))
            if available_nb:
                _log.error("  Available notebooks: %s", ', '.join(rtl_display(n) for n in sorted(available_nb)))
            return None
        filtered = [s for s in filtered if s.notebook in sanitized_nb_set]

    return filtered


def _collect_title(record: MigrationRecord, title_to_drive_file: dict[str, tuple[str, bool]] | None,
                   duplicate_titles: set[str] | None) -> None:
    """Add a successfully migrated note's title -> (file_id, is_doc) to the map."""
    if title_to_drive_file is None or record.status != MigrationStatus.SUCCESS or not record.output:
        return
    existing = title_to_drive_file.get(record.title)
    if existing and existing[0] != record.output[0] and duplicate_titles is not None:
        duplicate_titles.add(record.title)
    title_to_drive_file[record.title] = (record.output[0], record.is_doc)


def _run_migration(filtered: list[NotebookInfo], options: MigrationOptions, writer, seen: dict, records: list,
                   deferred_notes: list[DeferredInterlinkNote] | None, title_to_drive_file: dict[str, tuple[str, bool]] | None,
                   duplicate_titles: set[str] | None, renderer=None, total: int = 0) -> None:
    """Core migration loop, supporting both verbose and progress-bar display modes."""
    verbose = options.verbose
    gdrive = options.output_mode == OutputMode.GOOGLE
    ctx = _migration_progress() if not verbose else None

    with (ctx if ctx else _nullcontext()) as progress:
        task = progress.add_task("Migrating notes", total=total) if progress else None
        for info in filtered:
            if verbose:
                nb_start = time.monotonic()
                nb_count = 0
                nb_bytes = 0
                reset_throttle_sleep_total()
            for note in parse_enex(info.path, stack=info.stack):
                if options.note and note.title != options.note:
                    continue
                if verbose:
                    nb_bytes += len(note.enml.encode("utf-8")) if note.enml else 0
                    nb_bytes += sum(len(a.data) for a in note.attachments)
                else:
                    progress.update(task, description=f"[cyan]{rtl_display(note.notebook)}[/] / {rtl_display(note.title[:40])}")
                t0 = time.monotonic()
                record = migrate_note(note=note, options=options, writer=writer, seen=seen,
                                      deferred_notes=deferred_notes, renderer=renderer)
                elapsed = time.monotonic() - t0
                record.duration_s = elapsed
                records.append(record)
                _collect_title(record, title_to_drive_file, duplicate_titles)
                if verbose:
                    nb_count += 1
                    label = f"[cyan]{rtl_display(note.notebook)}[/] / {rtl_display(note.title)}"
                    if record.status == MigrationStatus.SKIPPED:
                        console.print(f"{label} - skipped")
                    elif gdrive:
                        console.print(f"{label} ({elapsed:.1f}s)")
                    else:
                        console.print(label)
                else:
                    progress.advance(task)
            if verbose:
                nb_elapsed = time.monotonic() - nb_start
                nb_mb = nb_bytes / (1024 * 1024)
                console.print(f"  [dim]{rtl_display(info.notebook)}: {nb_count} notes, {nb_elapsed:.1f}s, {nb_mb:.1f}MB")
                if gdrive:
                    log_throttle_summary(info.notebook, nb_elapsed)
        if progress:
            progress.update(task, description="[green]complete[/]")


def _rewrite_one_interlink(writer, deferred_note: DeferredInterlinkNote, title_to_drive_file: dict[str, tuple[str, bool]],
                 duplicate_titles: set[str]) -> tuple[int, int] | None:
    """Rewrite links for one deferred note. Returns (resolved, unresolved) or None on error."""
    try:
        return writer.rewrite_deferred_interlinks(deferred_note, title_to_drive_file, duplicate_titles)
    except HttpError as exc:
        _log.error("rewriting links for %s: %s", rtl_display(deferred_note.title), exc)
        return None


def _rewrite_deferred_interlinks(writer, deferred: list[DeferredInterlinkNote], title_to_drive_file: dict[str, tuple[str, bool]],
                        duplicate_titles: set[str], verbose: bool = False) -> None:
    total_resolved = total_unresolved = 0
    console.print()
    console.rule("pass 2")
    if verbose:
        for deferred_note in deferred:
            result = _rewrite_one_interlink(writer, deferred_note, title_to_drive_file, duplicate_titles)
            if result is not None:
                resolved, unresolved = result
                total_resolved += resolved
                total_unresolved += unresolved
                console.print(f"{rtl_display(deferred_note.title)} ({resolved + unresolved} links)")
    else:
        with _migration_progress() as progress:
            task = progress.add_task("Rewriting inter-note links", total=len(deferred))
            for deferred_note in deferred:
                progress.update(task, description=f"{rtl_display(deferred_note.title[:55])}")
                result = _rewrite_one_interlink(writer, deferred_note, title_to_drive_file, duplicate_titles)
                if result is not None:
                    total_resolved += result[0]
                    total_unresolved += result[1]
                progress.advance(task)
            progress.update(task, description="[green]complete[/]")
    console.print(f"  [dim]Links rewritten: {total_resolved} resolved, {total_unresolved} unresolved")


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
        from .drive_writer import GDriveWriter
        writer = GDriveWriter(options.dest, include_tags=options.include_tags, secrets_folder=options.secrets_folder,
                              modified_source=options.gdrive_modified)
    else:
        from .local_writer import LocalWriter
        writer = LocalWriter(Path(options.dest), include_tags=options.include_tags)

    records: list[MigrationRecord] = []
    seen: dict[tuple[str, str], int] = {}
    do_interlinks = not options.skip_note_links and not options.note
    deferred_notes: list | None = [] if do_interlinks else None
    title_to_drive_file: dict[str, tuple] | None = {} if do_interlinks else None
    duplicate_titles: set[str] | None = set() if do_interlinks else None

    from .webclip import WebClipRenderer
    renderer = WebClipRenderer(dark=options.clip_theme.value == "dark")

    try:
        total = 0 if options.verbose else count_notes(filtered)
        _run_migration(filtered, options, writer, seen, records, deferred_notes, title_to_drive_file,
                       duplicate_titles, renderer, total=total)
    finally:
        renderer.close()
        if null_tmp is not None:
            shutil.rmtree(null_tmp, ignore_errors=True)

    if deferred_notes and title_to_drive_file is not None:
        _rewrite_deferred_interlinks(writer, deferred_notes, title_to_drive_file, duplicate_titles or set(),
                            verbose=options.verbose)

    if options.note and not records:
        _log.error("note %s not found in the selected notebook(s)", rtl_display(options.note))
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
        writer.writerow(["notebook", "title", "kind", "status", "output_name", "embedded_images", "sibling_files", "error", "duration_s"])
        for r in records:
            writer.writerow([r.notebook, r.title, r.kind, r.status.value, r.output_name, r.embedded_images, r.sibling_files, r.error, f"{r.duration_s:.2f}"])


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
                _log.error("  - %s/%s: %s", rtl_display(r.notebook), rtl_display(r.title), r.error)
    if is_gdrive:
        mb = get_bytes_uploaded() / (1024 * 1024)
        console.print(f"  Uploaded:  ~{mb:.1f} MB (estimate)")
    console.print()

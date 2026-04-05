"""
CLI entry point for evernote-to-gdrive.
"""

from __future__ import annotations

import logging
from pathlib import Path

import click
import datetime

from .analyze import run_analysis
from .analyze_reports import (
    find_note, list_notes_by_mime, print_warnings, report_summary,
    report_attachments, report_classification, report_counts,
    report_duplicates, report_empty, report_tags, report_top_size,
)
from .report_links import report_links_notebooks, report_links_notes
from .migrate import AttachmentPolicy, MigrationOptions, OutputMode, run_migration
from .parser import load_notes

from ._console import console


@click.group()
def main():
    """Migrate Evernote notes to Google Drive."""


# ── analyze ───────────────────────────────────────────────────────────────────

_RESULT_REPORTS: dict = {
    'class':        report_classification,
    'attachments':  report_attachments,
    'counts':       report_counts,
    'top_size':     report_top_size,
}
_NOTES_REPORTS: dict = {
    'dups':             report_duplicates,
    'empty':            report_empty,
    'tags':             report_tags,
    'links_notebooks':  report_links_notebooks,
    'links_notes':      report_links_notes,
}
_ALL_ORDER = list(_RESULT_REPORTS) + list(_NOTES_REPORTS)


def _record_flags(key: str):
    def cb(ctx, _param, value):
        if value and not ctx.resilient_parsing:
            ctx.meta.setdefault('order', []).append((key, value))
        return value
    return cb


_STRING_REPORTS = {
    'mime':     list_notes_by_mime,
    'findnote': find_note,
}
_ALL_ORDER_PAIRS = [(k, True) for k in _ALL_ORDER]


@main.command()
@click.pass_context
@click.argument("input", type=click.Path(exists=True, path_type=Path))
@click.option("--all", "all_reports_flag", is_flag=True, default=False,
              help="Show all report sections.")
@click.option("--report-attachments", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('attachments'),
              help="Show attachment MIME types and totals.")
@click.option("--report-class", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('class'),
              help="Show note classification breakdown (text-only, attachment-only, mixed).")
@click.option("--report-counts", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('counts'),
              help="Show note counts per notebook.")
@click.option("--report-dups", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('dups'),
              help="List all notes with duplicate titles within the same notebook.")
@click.option("--report-empty", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('empty'),
              help="List all empty notes (no text and no attachments).")
@click.option("--report-links-notebooks", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('links_notebooks'),
              help="Report total inter-note link counts per notebook, sorted by count.")
@click.option("--report-links-notes", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('links_notes'),
              help="Report inter-note link counts per note, sorted by notebook then note name.")
@click.option("--report-tags", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('tags'),
              help="List all tags with a count of notes per tag, sorted by count.")
@click.option("--report-top-size", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('top_size'),
              help="Show top notebooks by attachment size.")
@click.option("--findnote", default=None, metavar="TITLE", expose_value=False,
              callback=_record_flags('findnote'),
              help="Report which notebook(s) contain a note with this title.")
@click.option("--mime", default=None, metavar="MIME_TYPE", expose_value=False,
              callback=_record_flags('mime'),
              help="List notes that have an attachment of this MIME type (e.g. application/msword).")
def analyze(ctx, input: Path, all_reports_flag: bool):
    """Inspect .enex files and report statistics (no upload).

    \b
    INPUT: path to a single .enex file, or a folder containing .enex files
           and subfolders (the folder structure mirrors Evernote stacks/notebooks).
    """
    console.print(f"[dim]Reading: {input}")
    notes = list(load_notes(input))

    order = _ALL_ORDER_PAIRS if all_reports_flag else ctx.meta.get('order', [])
    needs_result = not order or any(k in _RESULT_REPORTS for k, _ in order)
    result = run_analysis(notes) if needs_result else None

    if needs_result:
        report_summary(result)
    for key, value in order:
        if key in _RESULT_REPORTS:
            _RESULT_REPORTS[key](result)
        elif key in _NOTES_REPORTS:
            _NOTES_REPORTS[key](notes)
        else:
            _STRING_REPORTS[key](notes, value)
    if needs_result:
        print_warnings(result)


# ── migrate ───────────────────────────────────────────────────────────────────

@main.command()
@click.argument("input", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "output_mode",
              type=click.Choice(["gdrive", "local"], case_sensitive=False),
              default="gdrive", show_default=True,
              help="gdrive: upload to Google Drive. local: save to a local folder.")
@click.option("--dest", default="Evernote Migration", show_default=True,
              help="Output destination: Drive folder path (gdrive), local folder (local), or 'null' to run without writing files.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Authenticate and create root Drive folder only (gdrive mode only).")
@click.option("--stack", "stacks", multiple=True,
              help="Only migrate notebooks in this stack (repeatable).")
@click.option("--notebook", "notebooks", multiple=True,
              help="Only migrate this notebook (repeatable).")
@click.option("--note", default=None,
              help="Only migrate the note with this exact title (--notebook must also be specified).")
@click.option("--attachments",
              type=click.Choice(["doc", "files"], case_sensitive=False),
              default="doc", show_default=True,
              help="How to handle attachment-only notes with multiple non-image attachments: "
                   "doc=create a doc listing all attachments as sibling files (default); "
                   "files=sibling files only, no doc.")
@click.option("--log-file", type=click.Path(path_type=Path),
              default=None,
              help="Write migration log (CSV) to this file.")
@click.option("--no-tags", is_flag=True, default=False,
              help="Do not include Evernote tags in the output.")
@click.option("--verbose", is_flag=True, default=False,
              help="Print a line for each note instead of a progress bar.")
@click.option("--debug", is_flag=True, default=False,
              help="Enable debug logging of Google API calls.")
@click.option("--skip-note-links", is_flag=True, default=False,
              help="Skip rewriting evernote:/// inter-note links (GDrive mode only).")
def migrate(
    input: Path,
    output_mode: str,
    dest: str,
    dry_run: bool,
    stacks: tuple[str, ...],
    notebooks: tuple[str, ...],
    note: str | None,
    attachments: str,
    log_file: Path,
    no_tags: bool,
    verbose: bool,
    debug: bool,
    skip_note_links: bool,
):
    """Migrate Evernote notes to Google Drive (gdrive) or a local folder (local).

    \b
    INPUT: path to a single .enex file, or a folder containing .enex files
           and subfolders (the folder structure mirrors Evernote stacks/notebooks).
    """
    class _ConsoleHandler(logging.Handler):
        def emit(self, record):
            console.print(self.format(record), markup=False, highlight=False)

    handler = _ConsoleHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fmt.formatTime = lambda record, datefmt=None: (  # type: ignore[method-assign]
        datetime.datetime.fromtimestamp(record.created).strftime("%H:%M:%S.") +
        f"{int(record.msecs):03d}"
    )
    handler.setFormatter(fmt)
    pkg_log = logging.getLogger("evernote_to_google")
    pkg_log.setLevel(logging.DEBUG if debug else logging.INFO)
    pkg_log.propagate = False
    pkg_log.addHandler(handler)

    if note and not notebooks:
        raise click.UsageError("--note requires --notebook to be specified.")
    mode = OutputMode(output_mode.lower())

    options = MigrationOptions(
        output_mode=mode,
        dest=dest,
        dry_run=dry_run,
        stacks=list(stacks),
        notebooks=list(notebooks),
        note=note,
        attachments=AttachmentPolicy(attachments.lower()),
        log_file=log_file,
        include_tags=not no_tags,
        verbose=verbose or debug,
        skip_note_links=skip_note_links,
    )

    if mode == OutputMode.GOOGLE:
        if dry_run:
            console.print("[yellow]Dry run — only the root Drive folder will be created.")
        else:
            console.print(f"[dim]Writing to Google Drive folder: '{dest}'")
    else:
        if dest == "null":
            console.print("[dim]Null run — output is written to a temp dir and discarded.")
        else:
            console.print(f"[dim]Writing to local folder: '{Path(dest).resolve()}'")

    records = run_migration(input, options)

    if not records:
        console.print("[yellow]No notes migrated.[/]")
    elif mode == OutputMode.LOCAL:
        console.print("[green]Done.[/]")
    else:
        if log_file:
            console.print(f"[dim]Log written to '{log_file}'")

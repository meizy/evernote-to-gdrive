"""
CLI entry point for evernote-to-gdrive.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import click
from rich.console import Console

from .analyze import find_note, list_notes_by_mime, print_report, report_duplicates, report_tags as report_tags_fn, run_analysis
from .report_links import report_links_notebooks, report_links_notes
from .migrate import AttachmentPolicy, MigrationOptions, OutputMode, run_migration

console = Console()


@click.group()
def main():
    """Migrate Evernote notes to Google Drive."""


# ── analyze ───────────────────────────────────────────────────────────────────

@main.command()
@click.argument("input", type=click.Path(exists=True, path_type=Path))
@click.option("--output-json", type=click.Path(path_type=Path), default=None,
              help="Also write statistics to a JSON file.")
@click.option("--mime", default=None, metavar="MIME_TYPE",
              help="List notes that have an attachment of this MIME type (e.g. application/msword).")
@click.option("--findnote", default=None, metavar="TITLE",
              help="Report which notebook(s) contain a note with this title.")
@click.option("--report-dups", is_flag=True, default=False,
              help="List all notes with duplicate titles within the same notebook.")
@click.option("--report-tags", "report_tags", is_flag=True, default=False,
              help="List all tags with a count of notes per tag, sorted by count.")
@click.option("--report-links-notebooks", "report_links_nbs", is_flag=True, default=False,
              help="Report total inter-note link counts per notebook, sorted by count.")
@click.option("--report-links-notes", "report_links_nts", is_flag=True, default=False,
              help="Report inter-note link counts per note, sorted by notebook then note name.")
def analyze(input: Path, output_json: Path | None, mime: str | None, findnote: str | None, report_dups: bool, report_tags: bool, report_links_nbs: bool, report_links_nts: bool):
    """Inspect .enex files and report statistics (no upload)."""
    console.print(f"[dim]Reading: {input}")
    if mime:
        list_notes_by_mime(input, mime)
        return
    if findnote:
        find_note(input, findnote)
        return
    if report_dups:
        report_duplicates(input)
        return
    if report_tags:
        report_tags_fn(input)
        return
    if report_links_nbs:
        report_links_notebooks(input)
        return
    if report_links_nts:
        report_links_notes(input)
        return
    result = run_analysis(input)
    print_report(result)
    if output_json:
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
        output_json.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        console.print(f"[dim]Stats written to {output_json}")


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
              type=click.Choice(["doc", "files", "both"], case_sensitive=False),
              default="doc", show_default=True,
              help="How to handle attachments: "
                   "doc=embed images in doc + link PDFs (delete temp image files); "
                   "files=one raw file per attachment; "
                   "both=embed images in doc AND keep all as sibling files.")
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
    """Migrate Evernote notes to Google Drive (gdrive) or a local folder (local)."""
    if debug:
        handler = logging.StreamHandler()
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        fmt.formatTime = lambda record, datefmt=None: (  # type: ignore[method-assign]
            __import__("datetime").datetime.fromtimestamp(record.created).strftime("%H:%M:%S.") +
            f"{int(record.msecs):03d}"
        )
        handler.setFormatter(fmt)
        pkg_log = logging.getLogger("evernote_to_google")
        pkg_log.setLevel(logging.DEBUG)
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

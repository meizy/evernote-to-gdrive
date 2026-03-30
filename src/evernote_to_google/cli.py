"""
CLI entry point for evernote-to-gdrive.
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from .analyze import find_note, list_notes_by_mime, print_report, run_analysis, save_json
from .migrate import MigrationOptions, MultiAttachmentPolicy, OutputMode, run_migration

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
def analyze(input: Path, output_json: Path | None, mime: str | None, findnote: str | None):
    """Inspect .enex files and report statistics (no upload)."""
    console.print(f"[dim]Reading: {input}")
    if mime:
        list_notes_by_mime(input, mime)
        return
    if findnote:
        find_note(input, findnote)
        return
    result = run_analysis(input)
    print_report(result)
    if output_json:
        save_json(result, output_json)
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
@click.option("--skip-existing", is_flag=True, default=False,
              help="Skip notes whose output file already exists in the target folder.")
@click.option("--multi-attachment",
              type=click.Choice(["doc", "files"], case_sensitive=False),
              default="doc", show_default=True,
              help="How to handle notes with multiple images.")
@click.option("--log-file", type=click.Path(path_type=Path),
              default=None,
              help="Write migration log (CSV) to this file.")
@click.option("--verbose", is_flag=True, default=False,
              help="Print a line for each note instead of a progress bar.")
def migrate(
    input: Path,
    output_mode: str,
    dest: str,
    dry_run: bool,
    stacks: tuple[str, ...],
    notebooks: tuple[str, ...],
    note: str | None,
    skip_existing: bool,
    multi_attachment: str,
    log_file: Path,
    verbose: bool,
):
    """Migrate Evernote notes to Google Drive (gdrive) or a local folder (local)."""
    if note and not notebooks:
        raise click.UsageError("--note requires --notebook to be specified.")
    mode = OutputMode(output_mode.lower())

    options = MigrationOptions(
        output_mode=mode,
        dest=dest,
        dry_run=dry_run,
        skip_existing=skip_existing,
        stacks=list(stacks),
        notebooks=list(notebooks),
        note=note,
        multi_attachment=MultiAttachmentPolicy(multi_attachment.lower()),
        log_file=log_file,
        verbose=verbose,
    )

    drive, docs = None, None
    if mode == OutputMode.GOOGLE:
        if dry_run:
            console.print("[yellow]Dry run — only the root Drive folder will be created.")
        from .auth import get_services
        console.print("[dim]Authenticating with Google...")
        drive, docs = get_services()
        console.print("[green]Authenticated.")
    else:
        if dest == "null":
            console.print("[dim]Null run — output is written to a temp dir and discarded.")
        else:
            console.print(f"[dim]Writing to local folder: '{Path(dest).resolve()}'")

    records = run_migration(input, options, drive, docs)

    if not records:
        console.print("[yellow]No notes migrated.[/]")
    elif mode == OutputMode.LOCAL:
        console.print("[green]Done.[/]")
    else:
        if log_file:
            console.print(f"[dim]Log written to '{log_file}'")

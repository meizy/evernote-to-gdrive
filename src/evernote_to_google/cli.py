"""
CLI entry point for evernote-to-gdrive.
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from .analyze import print_report, run_analysis, save_json
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
def analyze(input: Path, output_json: Path | None):
    """Inspect .enex files and report statistics (no upload)."""
    console.print(f"[dim]Reading: {input}")
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
@click.option("--dest", default=None,
              help="Output destination: Drive folder path (gdrive, supports a/b/c) or local folder (local). "
                   "Defaults: 'Evernote Migration' (gdrive), 'evernote-export' (local).")
@click.option("--dry-run", is_flag=True, default=False,
              help="Authenticate and create root Drive folder only (api mode only).")
@click.option("--stack", "stacks", multiple=True,
              help="Only migrate notebooks in this stack (repeatable).")
@click.option("--notebook", "notebooks", multiple=True,
              help="Only migrate this notebook (repeatable).")
@click.option("--skip-existing", is_flag=True, default=False,
              help="Skip notes whose output file already exists in the target folder.")
@click.option("--multi-attachment",
              type=click.Choice(["doc", "files"], case_sensitive=False),
              default="doc", show_default=True,
              help="How to handle notes with multiple attachments.")
@click.option("--log-file", type=click.Path(path_type=Path),
              default="migration.log", show_default=True,
              help="Write migration log (CSV) to this file.")
def migrate(
    input: Path,
    output_mode: str,
    dest: str | None,
    dry_run: bool,
    stacks: tuple[str, ...],
    notebooks: tuple[str, ...],
    skip_existing: bool,
    multi_attachment: str,
    log_file: Path,
):
    """Migrate Evernote notes to Google Drive (gdrive) or a local folder (local)."""
    mode = OutputMode(output_mode.lower())

    if dest is None:
        dest = "Evernote Migration"

    options = MigrationOptions(
        output_mode=mode,
        dest=dest,
        dry_run=dry_run,
        skip_existing=skip_existing,
        stacks=list(stacks),
        notebooks=list(notebooks),
        multi_attachment=MultiAttachmentPolicy(multi_attachment.lower()),
        log_file=log_file,
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
        console.print(f"[dim]Writing to local folder: {Path(dest).resolve()}")

    run_migration(input, options, drive, docs)

    if mode == OutputMode.LOCAL:
        console.print(f"\n[green]Done.[/] Upload the folder [bold]{Path(dest).resolve()}[/] to Google Drive.")
        console.print("[dim]Tip: enable 'Convert uploads' in Drive settings to auto-convert .docx to Google Docs.")
    else:
        console.print(f"[dim]Log written to {log_file}")

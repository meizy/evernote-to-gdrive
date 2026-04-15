"""
CLI entry point for evernote-to-gdrive.
"""

from __future__ import annotations

import logging
from pathlib import Path

import click
import datetime

from .analyze import run_analysis
from .csv_table import set_csv_folder
from .analyze_reports import (
    find_note, list_attachments, list_clips, list_dups, list_empty, list_notes_by_mime,
    list_tags, print_warnings,
    report_attachments, report_classification, report_counts, report_summary, report_top_size,
)
from .analyze_links import list_links_notebooks, list_links_notes
from .migrate import AttachmentPolicy, MigrationOptions, OutputMode, run_migration
from .models import ClipTheme, GDriveModifiedSource, WebClipMode
from .parser import load_notes

from ._console import console
from .display import set_bidi


@click.group()
def main():
    """Migrate Evernote notes to Google Drive."""


# ── analyze ───────────────────────────────────────────────────────────────────

_RESULT_REPORTS: dict = {
    'summary':      report_summary,
    'class':        report_classification,
    'attachments':  report_attachments,
    'counts':       report_counts,
    'top_size':     report_top_size,
}
_NOTES_REPORTS: dict = {
    'clips':              list_clips,
    'dups':               list_dups,
    'empty':              list_empty,
    'tags':               list_tags,
    'links_notebooks':    list_links_notebooks,
    'links_notes':        list_links_notes,
    'list_attachments':   list_attachments,
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
@click.option("--report-summary", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('summary'),
              help="Show a summary of total notes, notebooks, and stacks.")
@click.option("--report-mime", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('attachments'),
              help="Show attachment MIME types and totals.")
@click.option("--report-class", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('class'),
              help="Show note classification breakdown (text-only, attachment-only, mixed).")
@click.option("--report-counts", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('counts'),
              help="Show note counts per notebook.")
@click.option("--list-clips", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('clips'),
              help="List all web clips (notes with a source URL).")
@click.option("--list-dups", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('dups'),
              help="List all notes with duplicate titles within the same notebook.")
@click.option("--list-empty", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('empty'),
              help="List all empty notes (no text and no attachments).")
@click.option("--list-links-notebooks", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('links_notebooks'),
              help="List total inter-note link counts per notebook, sorted by count.")
@click.option("--list-links-notes", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('links_notes'),
              help="List inter-note link counts per note, sorted by notebook then note name.")
@click.option("--list-tags", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('tags'),
              help="List all tags with a count of notes per tag, sorted by count.")
@click.option("--list-attachments", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('list_attachments'),
              help="List all notes with attachments: counts of images, PDFs, and other files per note.")
@click.option("--report-top-size", is_flag=True, default=False, expose_value=False,
              callback=_record_flags('top_size'),
              help="Show top notebooks by attachment size.")
@click.option("--write-csv", "csv_dir", default=None, metavar="DIR", type=click.Path(path_type=Path),
              help="Write each report table to a separate CSV file in DIR.")
@click.option("--findnote", default=None, metavar="TITLE", expose_value=False,
              callback=_record_flags('findnote'),
              help="Report which notebook(s) contain a note with this title.")
@click.option("--mime", default=None, metavar="MIME_TYPE", expose_value=False,
              callback=_record_flags('mime'),
              help="List notes that have an attachment of this MIME type (e.g. application/msword).")
@click.option("--bidi", type=click.BOOL, default=None, hidden=True)
def analyze(ctx, input: Path, all_reports_flag: bool, csv_dir: Path | None, bidi: bool | None):
    """Inspect .enex files and report statistics (no upload).

    \b
    INPUT: path to a single .enex file, or a folder containing .enex files
           and subfolders (the folder structure mirrors Evernote stacks/notebooks).
    """
    if bidi is not None:
        set_bidi(bidi)
    set_csv_folder(csv_dir)
    console.print(f"[dim]Reading: {input}")
    notes = list(load_notes(input))

    order = _ALL_ORDER_PAIRS if all_reports_flag else ctx.meta.get('order', [])
    if not order:
        order = [('summary', True)]
    needs_result = any(k in _RESULT_REPORTS for k, _ in order)
    result = run_analysis(notes) if needs_result else None

    for key, value in order:
        if key in _RESULT_REPORTS:
            _RESULT_REPORTS[key](result)
        elif key in _NOTES_REPORTS:
            _NOTES_REPORTS[key](notes)
        else:
            _STRING_REPORTS[key](notes, value)
    if needs_result:
        print_warnings(result)
    if csv_dir is not None:
        console.print(f"[dim]CSV output written to {csv_dir}")
    set_csv_folder(None)


# ── auth ──────────────────────────────────────────────────────────────────────

@main.command()
@click.option("--secrets-folder", type=click.Path(path_type=Path), default=None,
              help="Folder containing token.json and optional client_secrets.json. Default: current directory.")
def auth(secrets_folder: Path | None):
    """Authenticate with Google and save token.json."""
    from .auth import get_services, resolve_secrets_dir, token_path
    get_services(secrets_folder=secrets_folder)
    saved = token_path(resolve_secrets_dir(secrets_folder))
    console.print(f"[green]Authenticated.[/] Token saved to: {saved}")


# ── install-browsers ──────────────────────────────────────────────────────────

@main.command("install-browsers")
def install_browsers() -> None:
    """Install Chromium for web clip PDF rendering (wraps playwright install)."""
    import subprocess
    import sys
    console.print("[dim]Installing Chromium...[/]")
    pw_args = ["install"]
    if sys.platform == "linux":
        pw_args.append("--with-deps")
    pw_args.append("chromium")

    if getattr(sys, "frozen", False):
        from playwright.__main__ import main as pw_main
        saved_argv = sys.argv
        sys.argv = ["playwright", *pw_args]
        try:
            pw_main()
            rc = 0
        except SystemExit as e:
            rc = int(e.code or 0)
        finally:
            sys.argv = saved_argv
    else:
        result = subprocess.run([sys.executable, "-m", "playwright", *pw_args])
        rc = result.returncode

    if rc != 0:
        console.print("[red]Chromium installation failed.[/]")
        sys.exit(rc)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            version = browser.version
            browser.close()
        console.print(f"[green]Chromium {version} ready.[/]")
    except Exception:
        console.print("[green]Chromium installed successfully.[/]")


# ── migrate ───────────────────────────────────────────────────────────────────

def _configure_logging(debug: bool) -> None:
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
    pkg_log = logging.getLogger("evernote_to_gdrive")
    pkg_log.setLevel(logging.DEBUG if debug else logging.INFO)
    pkg_log.propagate = False
    pkg_log.addHandler(handler)


def _build_migration_options(
    output_mode: str,
    dest: str,
    force: bool,
    stacks: tuple[str, ...],
    notebooks: tuple[str, ...],
    note: str | None,
    attachments: str,
    log_file: Path,
    no_tags: bool,
    verbose: bool,
    debug: bool,
    skip_note_links: bool,
    web_clip: str | None,
    clip_theme: str,
    secrets_folder: Path | None,
    gdrive_modified: str,
) -> MigrationOptions:
    return MigrationOptions(
        output_mode=OutputMode(output_mode.lower()),
        dest=dest,
        stacks=list(stacks),
        notebooks=list(notebooks),
        note=note,
        attachments=AttachmentPolicy(attachments.lower()),
        log_file=log_file,
        secrets_folder=secrets_folder,
        include_tags=not no_tags,
        verbose=verbose or debug,
        skip_note_links=skip_note_links,
        web_clip=WebClipMode(web_clip.lower()),
        clip_theme=ClipTheme(clip_theme.lower()),
        force=force,
        gdrive_modified=GDriveModifiedSource(gdrive_modified.lower()),
    )


@main.command()
@click.argument("input", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "output_mode",
              type=click.Choice(["gdrive", "local"], case_sensitive=False),
              default="gdrive", show_default=True,
              help="gdrive: upload to Google Drive. local: save to a local folder.")
@click.option("--dest", default="Evernote Migration", show_default=True,
              help="Output destination: Drive folder path (gdrive), local folder (local), or 'null' to run without writing files.")
@click.option("--force", is_flag=True, default=False,
              help="Skip existence checks and re-export all notes, overwriting existing files.")
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
              help="Skip rewriting evernote:/// inter-note links.")
@click.option("--web-clip", "web_clip",
              type=click.Choice(["pdf", "doc"], case_sensitive=False),
              default="pdf", show_default=True,
              help="Output format for web clip notes (those with a source URL): "
                   "pdf=render as a clean Reader-style PDF, doc=create doc from cleaned HTML.")
@click.option("--clip-theme",
              type=click.Choice(["light", "dark"], case_sensitive=False),
              default="light", show_default=True,
              help="Theme for web clip rendering.")
@click.option("--secrets-folder", type=click.Path(path_type=Path), default=None,
              help="Folder containing token.json and optional client_secrets.json. Default: current directory.")
@click.option("--gdrive-modified",
              type=click.Choice(["created", "updated"], case_sensitive=False),
              default="created", show_default=True,
              help="Timestamp used as Drive modifiedTime (gdrive only): created=note's original date (default), updated=Evernote last-modified.")
@click.option("--bidi", type=click.BOOL, default=None, hidden=True)
def migrate(
    input: Path,
    output_mode: str,
    dest: str,
    force: bool,
    stacks: tuple[str, ...],
    notebooks: tuple[str, ...],
    note: str | None,
    attachments: str,
    log_file: Path,
    no_tags: bool,
    verbose: bool,
    debug: bool,
    skip_note_links: bool,
    web_clip: str | None,
    clip_theme: str,
    secrets_folder: Path | None,
    gdrive_modified: str,
    bidi: bool | None,
):
    """Migrate Evernote notes to Google Drive (gdrive) or a local folder (local).

    \b
    INPUT: path to a single .enex file, or a folder containing .enex files
           and subfolders (the folder structure mirrors Evernote stacks/notebooks).
    """
    if bidi is not None:
        set_bidi(bidi)
    _configure_logging(debug)

    if note and not notebooks:
        raise click.UsageError("--note requires --notebook to be specified.")
    mode = OutputMode(output_mode.lower())

    options = _build_migration_options(
        output_mode=output_mode,
        dest=dest,
        force=force,
        stacks=stacks,
        notebooks=notebooks,
        note=note,
        attachments=attachments,
        log_file=log_file,
        no_tags=no_tags,
        verbose=verbose,
        debug=debug,
        skip_note_links=skip_note_links,
        web_clip=web_clip,
        clip_theme=clip_theme,
        secrets_folder=secrets_folder,
        gdrive_modified=gdrive_modified,
    )

    if mode == OutputMode.GOOGLE:
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

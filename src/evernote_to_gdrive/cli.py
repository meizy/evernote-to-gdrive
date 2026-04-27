"""
CLI entry point for evernote-to-gdrive.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import click

from ._startup import configure_logging, log_startup
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

from ._runtime_paths import cwd_cache_dir
from .display import set_rtl_mode

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(cwd_cache_dir() / "playwright-browsers"))

_log = logging.getLogger("evernote_to_gdrive")


@click.group()
def main():
    """Migrate Evernote notes to Google Drive or local file system."""


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
@click.option("--include-zero", is_flag=True, default=False,
              help="With --list-attachments, also include notes that have no attachments (all zeros).")
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
@click.option("--debug", is_flag=True, default=False,
              help="Enable debug logging (prints version, environment, and parsed params at startup).")
@click.option("--rtl", type=click.Choice(["auto", "wrap", "reverse", "off"]), default="auto", hidden=True)
def analyze(ctx, input: Path, all_reports_flag: bool, csv_dir: Path | None, include_zero: bool, debug: bool, rtl: str):
    """Inspect .enex files and report statistics (no upload).

    \b
    INPUT: path to a single .enex file, or a folder containing .enex files
           and subfolders (the folder structure mirrors Evernote stacks/notebooks).
    """
    set_rtl_mode(rtl)
    configure_logging(debug)
    log_startup(ctx)
    set_csv_folder(csv_dir)
    _log.info("Reading: %s", input)
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
            if key == 'list_attachments':
                _NOTES_REPORTS[key](notes, include_zero=include_zero)
            else:
                _NOTES_REPORTS[key](notes)
        else:
            _STRING_REPORTS[key](notes, value)
    if needs_result:
        print_warnings(result)
    if csv_dir is not None:
        _log.info("CSV output written to %s", csv_dir)
    set_csv_folder(None)


# ── auth ──────────────────────────────────────────────────────────────────────

@main.command()
@click.pass_context
@click.option("--secrets-folder", type=click.Path(path_type=Path), default=None,
              help="Folder containing token.json and optional client_secrets.json. Default: current directory.")
@click.option("--debug", is_flag=True, default=False,
              help="Enable debug logging (prints version, environment, and parsed params at startup).")
def auth(ctx, secrets_folder: Path | None, debug: bool):
    """Authenticate with Google and save token.json."""
    configure_logging(debug)
    log_startup(ctx)
    from .auth import get_services, resolve_secrets_dir, token_path
    get_services(secrets_folder=secrets_folder)
    saved = token_path(resolve_secrets_dir(secrets_folder))
    _log.info("Authenticated. Token saved to: %s", saved)


# ── install-browsers ──────────────────────────────────────────────────────────

@main.command("install-browsers")
@click.pass_context
@click.option("--debug", is_flag=True, default=False,
              help="Enable debug logging (prints version, environment, and parsed params at startup).")
def install_browsers(ctx, debug: bool) -> None:
    """Install Chromium for web clip PDF rendering (wraps playwright install)."""
    import subprocess
    import sys

    configure_logging(debug)
    log_startup(ctx)
    _log.info("Installing Chromium headless shell...")
    pw_args = ["install", "--only-shell"]
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
        _log.error("Chromium headless shell installation failed.")
        sys.exit(rc)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            version = browser.version
            browser.close()
        _log.info("Chromium %s ready.", version)
    except Exception:
        _log.info("Chromium installed successfully.")


# ── migrate ───────────────────────────────────────────────────────────────────

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
        verbose=verbose,
        debug=debug,
        skip_note_links=skip_note_links,
        web_clip=WebClipMode(web_clip.lower()),
        clip_theme=ClipTheme(clip_theme.lower()),
        force=force,
        gdrive_modified=GDriveModifiedSource(gdrive_modified.lower()),
    )


@main.command()
@click.pass_context
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
@click.option("--rtl", type=click.Choice(["auto", "wrap", "reverse", "off"]), default="auto", hidden=True)
def migrate(
    ctx,
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
    rtl: str,
):
    """Migrate Evernote notes to Google Drive (gdrive) or a local folder (local).

    \b
    INPUT: path to a single .enex file, or a folder containing .enex files
           and subfolders (the folder structure mirrors Evernote stacks/notebooks).
    """
    set_rtl_mode(rtl)
    configure_logging(debug)
    log_startup(ctx)

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
        _log.info("Writing to Google Drive folder: '%s'", dest)
    else:
        if dest == "null":
            _log.info("Null run — output is written to a temp dir and discarded.")
        else:
            _log.info("Writing to local folder: '%s'", Path(dest).resolve())

    records = run_migration(input, options)

    if not records:
        _log.warning("No notes migrated.")
    elif mode == OutputMode.LOCAL:
        _log.info("Done.")
    else:
        if log_file:
            _log.info("Log written to '%s'", log_file)

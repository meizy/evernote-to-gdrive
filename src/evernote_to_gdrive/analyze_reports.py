"""
Report functions for Evernote export analysis.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .csv_table import CsvTable as Table

from .analyze import AnalysisResult
from .classifier import classify, _safe_name, _SKIP_MIME, _EMBEDDABLE_IMAGE_MIME
from .display import format_notebook, rtl_display
from .parser import Note

from ._console import console


# ── print_report section functions ────────────────────────────────────────────

def report_summary(result: AnalysisResult) -> None:
    t = Table(title="Evernote Export Summary", show_header=True)
    t.add_column("Metric", style="bold cyan", no_wrap=True)
    t.add_column("Value", justify="right")
    t.add_row("Total notes", str(result.total_notes))
    t.add_row("Notebooks", str(len(result.by_notebook)))
    if result.stacks:
        t.add_row("Stacks", str(len(result.stacks)))
    console.print()
    console.print(t)
    console.print()


def report_classification(result: AnalysisResult) -> None:
    t = Table(title="Note Classification", show_lines=False)
    t.add_column("Type", style="bold")
    t.add_column("Count", justify="right")
    t.add_column("Output")
    t.add_row("Text only", str(result.text_only), "Document")
    t.add_row("Attachment only (1 file)", str(result.attachment_only_single), "File")
    t.add_row("Attachment only (multi)", str(result.attachment_only_multi), "Document (links) + files")
    t.add_row("Text + attachment(s)", str(result.text_with_attachments), "Document + files")
    console.print(t)
    console.print()


def report_attachments(result: AnalysisResult) -> None:
    if not result.attachments.count:
        return
    t = Table(title="Attachments")
    t.add_column("MIME type", style="bold")
    t.add_column("Count", justify="right")
    excluded = sorted(_SKIP_MIME & result.attachments.by_mime.keys())
    for mime, cnt in sorted(result.attachments.by_mime.items()):
        row_style = "dim" if mime in _SKIP_MIME else ""
        t.add_row(mime, str(cnt), style=row_style)
    console.print(t)
    if excluded:
        console.print(f"[dim]  Note: {', '.join(excluded)} will be excluded from migration.")
    console.print()

    total_mb = result.attachments.total_bytes / 1_048_576
    largest_mb = result.attachments.largest_bytes / 1_048_576
    s = Table(show_header=False, box=None, padding=(0, 2))
    s.add_column(style="bold cyan", no_wrap=True)
    s.add_column()
    s.add_row("Total attachments", str(result.attachments.count))
    s.add_row("Total size", f"{total_mb:.1f} MB")
    s.add_row("Largest attachment", f"{largest_mb:.1f} MB  ({result.attachments.largest_name})")
    s.add_row("Notes with multiple attachments", str(result.notes_with_multi_attachments))
    console.print(s)
    console.print()


def report_counts(result: AnalysisResult) -> None:
    t = Table(title="Notes per Notebook")
    t.add_column("Notebook", style="bold")
    t.add_column("Notes", justify="right")
    for (stack, nb), cnt in sorted(result.by_notebook.items(), key=lambda x: (x[0][0] or "", x[0][1])):
        t.add_row(format_notebook(stack, nb), str(cnt))
    console.print(t)
    console.print()


def report_top_size(result: AnalysisResult) -> None:
    if not result.attachment_bytes_by_notebook:
        return
    top = sorted(result.attachment_bytes_by_notebook.items(), key=lambda x: x[1], reverse=True)[:10]
    t = Table(title="Top Notebooks by Attachment Size")
    t.add_column("Notebook", style="bold")
    t.add_column("Total Size", justify="right")
    for (stack, nb), nbytes in top:
        t.add_row(format_notebook(stack, nb), f"{nbytes / 1_048_576:.1f} MB")
    console.print(t)
    console.print()


def print_warnings(result: AnalysisResult) -> None:
    if not result.empty_notes and not result.encrypted_notes:
        return
    console.rule("[yellow]Warnings")
    if result.empty_notes:
        console.print(f"[yellow]  {result.empty_notes} empty note(s) (no text, no attachments)")
    if result.encrypted_notes:
        console.print(
            f"[yellow]  {result.encrypted_notes} note(s) contain encrypted sections "
            "(encrypted blocks will be stripped from the output)"
        )
    console.print()


# ── standalone report functions ───────────────────────────────────────────────

def list_notes_by_mime(notes: Iterable[Note], mime_type: str) -> None:
    matches: list[tuple[str | None, str, str, list[str]]] = []
    for note in notes:
        matched = [att.filename or "<unnamed>" for att in note.attachments if att.mime == mime_type]
        if matched:
            matches.append((note.stack, note.notebook, note.title, matched))

    if not matches:
        console.print(f"[yellow]No notes found with MIME type: {mime_type}")
        return

    t = Table(title=f"Notes with attachment type: {mime_type}")
    t.add_column("Notebook", style="bold")
    t.add_column("Note title")
    t.add_column("Matching files")
    for stack, notebook, title, filenames in sorted(matches, key=lambda x: (x[0] or "", x[1], x[2])):
        t.add_row(format_notebook(stack, notebook), rtl_display(title), ", ".join(filenames))
    console.print()
    console.print(t)
    console.print(f"\n[dim]{len(matches)} note(s) matched.")


def find_note(notes: Iterable[Note], title: str) -> None:
    matches: list[tuple[str | None, str]] = []
    for note in notes:
        if note.title == title:
            matches.append((note.stack, note.notebook))

    if not matches:
        console.print(f"[yellow]No note found with title: {title!r}")
        return

    t = Table(title=f"Note: {title!r}")
    t.add_column("Notebook", style="bold")
    for stack, notebook in sorted(matches, key=lambda x: (x[0] or "", x[1])):
        t.add_row(format_notebook(stack, notebook))
    console.print()
    console.print(t)
    console.print(f"\n[dim]{len(matches)} match(es).")


def list_dups(notes: Iterable[Note]) -> None:
    groups: dict[tuple[str | None, str, str], list[str]] = defaultdict(list)
    for note in notes:
        groups[(note.stack, note.notebook, _safe_name(note.title))].append(note.title)

    dups = {k: v for k, v in groups.items() if len(v) > 1}
    if not dups:
        console.print("[green]No duplicate note titles found.")
        return

    t = Table(title="Duplicate Notes (same notebook + same safe title)")
    t.add_column("Notebook", style="bold")
    t.add_column("Safe Title")
    t.add_column("Count", justify="right")
    t.add_column("Original Titles")
    for (stack, notebook, safe_title), titles in sorted(dups.items(), key=lambda x: (x[0][0] or "", x[0][1], x[0][2])):
        t.add_row(
            format_notebook(stack, notebook),
            rtl_display(safe_title),
            str(len(titles)),
            "\n".join(rtl_display(t_) for t_ in dict.fromkeys(titles)),
        )
    console.print()
    console.print(t)
    total = sum(len(v) - 1 for v in dups.values())
    console.print(f"\n[yellow]{len(dups)} group(s) with duplicates — {total} note(s) will be renamed during migration (local: ' (2)' suffix; gdrive: same name kept).")


def list_empty(notes: Iterable[Note]) -> None:
    matches: list[tuple[str | None, str, str]] = []
    for note in notes:
        classified = classify(note)
        if not classified.plain_text and not note.attachments:
            matches.append((note.stack, note.notebook, note.title))

    if not matches:
        console.print("[green]No empty notes found.")
        return

    t = Table(title="Empty Notes (no text, no attachments)")
    t.add_column("Notebook", style="bold")
    t.add_column("Title")
    for stack, notebook, title in sorted(matches, key=lambda x: (x[0] or "", x[1], x[2])):
        t.add_row(format_notebook(stack, notebook), rtl_display(title))
    console.print()
    console.print(t)
    console.print(f"\n[dim]{len(matches)} empty note(s).")


def list_clips(notes: Iterable[Note]) -> None:
    matches: list[tuple[str, str, str, str]] = []
    for note in notes:
        if note.source_url is not None:
            nb = format_notebook(note.stack, note.notebook)
            matches.append((nb, note.notebook, note.title, note.source_url))

    if not matches:
        console.print("[green]No web clips found (no notes with a source URL).")
        return

    t = Table(title="Web Clips (notes with a source URL)")
    t.add_column("Notebook", style="bold")
    t.add_column("Note title")
    t.add_column("Source URL")
    for nb, _notebook, title, url in sorted(matches):
        t.add_row(nb, rtl_display(title), url)
    console.print()
    console.print(t)
    console.print(f"\n[dim]{len(matches)} web clip(s).")


def list_attachments(notes: Iterable[Note], include_zero: bool = False) -> None:
    rows: list[tuple[str, str, bool, int, int, int, bool]] = []
    for note in notes:
        classified = classify(note)
        attachments = classified.attachments
        if not attachments and not include_zero:
            continue
        has_text = bool(classified.plain_text)
        images = sum(1 for a in attachments if a.mime in _EMBEDDABLE_IMAGE_MIME)
        pdfs = sum(1 for a in attachments if a.mime == "application/pdf")
        other = len(attachments) - images - pdfs
        nb = format_notebook(note.stack, note.notebook)
        rows.append((nb, note.title, has_text, images, pdfs, other, note.source_url is not None))

    if not rows:
        console.print("[yellow]No notes with attachments found.")
        return

    t = Table(title="Notes with Attachments")
    t.add_column("Notebook", style="bold")
    t.add_column("Note title")
    t.add_column("Text", justify="center")
    t.add_column("Images", justify="right")
    t.add_column("PDFs", justify="right")
    t.add_column("Other", justify="right")
    t.add_column("Web Clip", justify="center")
    for nb, title, has_text, images, pdfs, other, web_clip in sorted(rows):
        t.add_row(nb, rtl_display(title), "Y" if has_text else "N",
                  str(images), str(pdfs), str(other), "Y" if web_clip else "")
    console.print()
    console.print(t)
    suffix = "note(s)" if include_zero else "note(s) with attachments"
    console.print(f"\n[dim]{len(rows)} {suffix}.")


def list_tags(notes: Iterable[Note]) -> None:
    tag_counts: dict[str, int] = defaultdict(int)
    for note in notes:
        for tag in note.tags:
            tag_counts[tag] += 1

    if not tag_counts:
        console.print("[yellow]No tags found.")
        return

    t = Table(title="Tags by Note Count")
    t.add_column("Tag", style="bold")
    t.add_column("Notes", justify="right")
    for tag, count in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True):
        t.add_row(rtl_display(tag), str(count))
    console.print()
    console.print(t)
    console.print(f"\n[dim]{len(tag_counts)} unique tag(s).")

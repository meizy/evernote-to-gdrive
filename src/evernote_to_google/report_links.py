"""
Report inter-note link counts from .enex files.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .display import rtl_display
from .interlinks import count_interlinks
from .parser import load_notes

console = Console()


def report_links_notebooks(input_path: Path) -> None:
    """Report total inter-note link counts per notebook, sorted by count descending."""
    notebook_counts: dict[str, int] = defaultdict(int)

    for note in load_notes(input_path):
        n = count_interlinks(note.enml)
        if n:
            notebook_counts[note.notebook] += n

    if not notebook_counts:
        console.print("[yellow]No inter-note links found.")
        return

    table = Table(title="Inter-note Links by Notebook")
    table.add_column("Notebook", style="bold")
    table.add_column("Links", justify="right")
    for nb, cnt in sorted(notebook_counts.items(), key=lambda x: x[1], reverse=True):
        table.add_row(rtl_display(nb), str(cnt))
    console.print()
    console.print(table)
    total = sum(notebook_counts.values())
    console.print(f"\n[dim]{total} link(s) across {len(notebook_counts)} notebook(s).")


def report_links_notes(input_path: Path) -> None:
    """Report inter-note link counts per note, sorted by notebook then note name."""
    note_rows: list[tuple[str, str, int]] = []  # (notebook, title, count)

    for note in load_notes(input_path):
        n = count_interlinks(note.enml)
        if n:
            note_rows.append((note.notebook, note.title, n))

    if not note_rows:
        console.print("[yellow]No inter-note links found.")
        return

    table = Table(title="Inter-note Links by Note")
    table.add_column("Notebook", style="bold")
    table.add_column("Note")
    table.add_column("Links", justify="right")
    for nb, title, cnt in sorted(note_rows, key=lambda x: (x[0], x[1])):
        table.add_row(rtl_display(nb), rtl_display(title), str(cnt))
    console.print()
    console.print(table)
    total = sum(r[2] for r in note_rows)
    console.print(f"\n[dim]{total} link(s) across {len(note_rows)} note(s).")

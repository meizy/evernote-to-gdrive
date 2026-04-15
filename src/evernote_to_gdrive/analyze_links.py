"""
Report inter-note link counts from .enex files.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .csv_table import CsvTable as Table

from .display import format_notebook, rtl_display
from .interlinks import count_interlinks
from .parser import Note

from ._console import console


def list_links_notebooks(notes: Iterable[Note]) -> None:
    """Report total inter-note link counts per notebook, sorted by count descending."""
    notebook_counts: dict[tuple[str | None, str], int] = defaultdict(int)

    for note in notes:
        n = count_interlinks(note.enml)
        if n:
            notebook_counts[(note.stack, note.notebook)] += n

    if not notebook_counts:
        console.print("[yellow]No inter-note links found.")
        return

    table = Table(title="Inter-note Links by Notebook")
    table.add_column("Notebook", style="bold")
    table.add_column("Links", justify="right")
    for (stack, nb), cnt in sorted(notebook_counts.items(), key=lambda x: x[1], reverse=True):
        table.add_row(format_notebook(stack, nb), str(cnt))
    console.print()
    console.print(table)
    total = sum(notebook_counts.values())
    console.print(f"\n[dim]{total} link(s) across {len(notebook_counts)} notebook(s).")


def list_links_notes(notes: Iterable[Note]) -> None:
    """Report inter-note link counts per note, sorted by notebook then note name."""
    note_rows: list[tuple[str | None, str, str, int]] = []

    for note in notes:
        n = count_interlinks(note.enml)
        if n:
            note_rows.append((note.stack, note.notebook, note.title, n))

    if not note_rows:
        console.print("[yellow]No inter-note links found.")
        return

    table = Table(title="Inter-note Links by Note")
    table.add_column("Notebook", style="bold")
    table.add_column("Note")
    table.add_column("Links", justify="right")
    for stack, nb, title, cnt in sorted(note_rows, key=lambda x: (x[0] or "", x[1], x[2])):
        table.add_row(format_notebook(stack, nb), rtl_display(title), str(cnt))
    console.print()
    console.print(table)
    total = sum(r[3] for r in note_rows)
    console.print(f"\n[dim]{total} link(s) across {len(note_rows)} note(s).")

"""
CsvTable: a rich.Table subclass that also writes a CSV file on render.

Usage:
    set_csv_folder(Path("output/csv"))  # activate CSV writing
    # All CsvTable instances rendered after this will write to that folder.
    set_csv_folder(None)                # deactivate
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from rich.console import Console, ConsoleOptions, RenderResult
from rich.table import Table

_folder: Path | None = None


def set_csv_folder(path: Path | None) -> None:
    global _folder
    _folder = path


def _slug(title: str) -> str:
    clean = re.sub(r'\[.*?\]', '', title)       # strip rich markup
    return re.sub(r'[^a-z0-9]+', '_', clean.lower()).strip('_')


class CsvTable(Table):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rows_data: list[list[str]] = []

    def add_row(self, *renderables, **kwargs):
        self._rows_data.append([str(r) if r is not None else '' for r in renderables])
        super().add_row(*renderables, **kwargs)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        if _folder is not None and self.title and self._rows_data:
            _write_csv(_folder, self)
        yield from super().__rich_console__(console, options)


def _write_csv(folder: Path, table: CsvTable) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    filename = _slug(str(table.title)) + '.csv'
    headers = [str(col.header) for col in table.columns]
    with (folder / filename).open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if headers:
            writer.writerow(headers)
        writer.writerows(table._rows_data)

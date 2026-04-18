"""
Analyze command e2e tests: invoke the CLI with --write-csv and validate
the resulting CSV files for correctness.
"""

import csv
from pathlib import Path

import pytest
from click.testing import CliRunner

from evernote_to_gdrive.cli import main

from helpers import FIXTURES_DIR


@pytest.fixture(scope="module")
def analyze_csv(tmp_path_factory) -> Path:
    csv_dir = tmp_path_factory.mktemp("analyze_csv")
    runner = CliRunner()
    result = runner.invoke(main, [
        "analyze", str(FIXTURES_DIR),
        "--report-summary",
        "--list-dups",
        "--write-csv", str(csv_dir),
    ])
    assert result.exit_code == 0, f"analyze command failed:\n{result.output}"
    return csv_dir


def _read_csv(path: Path) -> list[dict]:
    return list(csv.DictReader(path.open(encoding="utf-8")))


@pytest.mark.local
def test_report_summary_counts(analyze_csv):
    # CsvTable slugifies "Evernote Export Summary" → evernote_export_summary.csv
    rows = _read_csv(analyze_csv / "evernote_export_summary.csv")
    by_metric = {r["Metric"]: r["Value"] for r in rows}
    assert by_metric["Total notes"] == "14", f"Unexpected summary: {by_metric}"
    assert by_metric["Notebooks"] == "2", f"Unexpected summary: {by_metric}"


@pytest.mark.local
def test_list_dups(analyze_csv):
    # "Duplicate Notes (same notebook + same safe title)" →
    # duplicate_notes_same_notebook_same_safe_title.csv
    csv_file = analyze_csv / "duplicate_notes_same_notebook_same_safe_title.csv"
    assert csv_file.exists(), f"Dups CSV not found. Files: {list(analyze_csv.iterdir())}"
    rows = _read_csv(csv_file)
    titles = [r["Safe Title"] for r in rows]
    assert any("Text With Mixed Attachments" in t for t in titles), (
        f"Expected duplicate title not found in CSV rows: {titles}"
    )

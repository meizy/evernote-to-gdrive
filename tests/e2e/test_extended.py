"""
Extended e2e tests: special title chars, custom MIME, en-crypt stripping,
checkbox conversion, and log-file CSV output.

A single module-scoped migration run is shared across all assertions.
"""

import csv
from pathlib import Path

import pytest

from evernote_to_gdrive.models import MigrationRecord

from helpers import FIXTURES_EXTENDED_DIR, docx_text, make_local_options
from evernote_to_gdrive.migrate import run_migration


@pytest.fixture(scope="module")
def extended_output(tmp_path_factory) -> tuple[Path, list[MigrationRecord], Path]:
    out = tmp_path_factory.mktemp("extended")
    log = out / "migration.csv"
    options = make_local_options(out, log_file=log)
    records = run_migration(FIXTURES_EXTENDED_DIR, options)
    return out, records, log



@pytest.mark.local
def test_special_char_title(extended_output):
    out, _, _ = extended_output
    nb = out / "Extended Notebook"
    expected = nb / "note_ with special char.docx"
    assert expected.exists(), f"Expected {expected}, found: {list(nb.iterdir())}"


@pytest.mark.local
def test_custom_mime_extension(extended_output):
    out, _, _ = extended_output
    nb = out / "Extended Notebook"
    matches = list(nb.glob("Custom Mime Attachment.*"))
    assert matches, f"No output file for 'Custom Mime Attachment' in {list(nb.iterdir())}"
    assert matches[0].suffix == ".custom", f"Expected .custom extension, got {matches[0].suffix}"


@pytest.mark.local
def test_en_crypt_stripped(extended_output):
    out, _, _ = extended_output
    path = out / "Extended Notebook" / "Encrypted Content.docx"
    text = docx_text(path)
    assert "secret stuff" not in text, f"en-crypt content leaked into docx: {text!r}"
    assert "Visible text" in text or "Also visible" in text, (
        f"Surrounding text missing from docx: {text!r}"
    )


@pytest.mark.local
def test_checkboxes_converted(extended_output):
    out, _, _ = extended_output
    path = out / "Extended Notebook" / "Checkbox Note.docx"
    text = docx_text(path)
    assert "[x]" in text, f"Checked checkbox marker '[x]' not found in: {text!r}"
    assert "[ ]" in text or "[\xa0]" in text, f"Unchecked checkbox marker not found in: {text!r}"


@pytest.mark.local
def test_log_file_valid_csv(extended_output):
    _, records, log = extended_output
    assert log.exists(), f"Log file not created at {log}"
    rows = list(csv.DictReader(log.open(encoding="utf-8")))
    expected_cols = {"notebook", "title", "kind", "status", "output_name", "embedded_images", "sibling_files", "error", "duration_s"}
    assert expected_cols.issubset(rows[0].keys()), (
        f"Missing columns. Got: {set(rows[0].keys())}"
    )
    assert len(rows) == len(records), (
        f"CSV has {len(rows)} rows but migration produced {len(records)} records"
    )
    assert all(r["status"] == "success" for r in rows), (
        f"Not all rows are success: {[r['status'] for r in rows]}"
    )

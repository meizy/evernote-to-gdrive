"""
Migrate non-default flags e2e tests.

Each test runs an independent migration into its own tmp_path.
"""

from pathlib import Path

import pytest

from evernote_to_gdrive.migrate import run_migration
from evernote_to_gdrive.models import AttachmentPolicy, MigrationStatus

from helpers import FIXTURES_DIR, docx_text, make_local_options


@pytest.mark.local
def test_attachments_files_policy(tmp_path):
    options = make_local_options(tmp_path, attachments=AttachmentPolicy.FILES)
    run_migration(FIXTURES_DIR, options)
    nb = tmp_path / "Sanity Notebook"
    # FILES policy: multi-PDF note should have no _0.docx wrapper doc
    assert not (nb / "Multiple PDF Attachments_0.docx").exists(), (
        "Unexpected _0.docx created under FILES policy"
    )
    # But the PDF siblings should still exist
    assert (nb / "Multiple PDF Attachments_1.pdf").exists()
    assert (nb / "Multiple PDF Attachments_2.pdf").exists()


@pytest.mark.local
def test_no_tags(tmp_path):
    options = make_local_options(tmp_path, include_tags=False)
    run_migration(FIXTURES_DIR, options)
    path = tmp_path / "Sanity Notebook" / "Text Only With Tags.docx"
    text = docx_text(path)
    assert "tag:" not in text, f"Tags unexpectedly present in docx:\n{text}"


@pytest.mark.local
def test_notebook_filter(tmp_path):
    options = make_local_options(tmp_path, notebooks=["Stacked Notebook"])
    records = run_migration(FIXTURES_DIR, options)
    assert len(records) == 1, f"Expected 1 record, got {len(records)}"
    assert not (tmp_path / "Sanity Notebook").exists(), (
        "Sanity Notebook directory should not exist with notebook filter"
    )
    assert (tmp_path / "Test Stack" / "Stacked Notebook" / "Note In Stack.docx").exists()


@pytest.mark.local
def test_single_note_mode(tmp_path):
    options = make_local_options(tmp_path, notebooks=["Sanity Notebook"], note="Text Only")
    records = run_migration(FIXTURES_DIR, options)
    assert len(records) == 1, f"Expected 1 record, got {len(records)}"
    assert records[0].title == "Text Only"
    assert (tmp_path / "Sanity Notebook" / "Text Only.docx").exists()


@pytest.mark.local
def test_bad_notebook_filter(tmp_path):
    options = make_local_options(tmp_path, notebooks=["Nonexistent Notebook"])
    records = run_migration(FIXTURES_DIR, options)
    assert records == [], f"Expected empty list for bad filter, got {records}"


@pytest.mark.local
def test_full_migration_with_progress_mode(tmp_path):
    options = make_local_options(tmp_path, verbose=False)
    records = run_migration(FIXTURES_DIR, options)
    assert len(records) == 14, f"Expected 14 records, got {len(records)}"
    assert (tmp_path / "Sanity Notebook" / "Text Only.docx").exists()
    assert (tmp_path / "Test Stack" / "Stacked Notebook" / "Note In Stack.docx").exists()

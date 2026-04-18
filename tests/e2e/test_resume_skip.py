"""
Resume/skip behavior e2e tests.

The tool uses stateless destination-probing: before each note it checks
whether the output already exists. A second run skips all notes that were
successfully written in the first run.
"""

from pathlib import Path

import pytest

from evernote_to_gdrive.migrate import run_migration
from evernote_to_gdrive.models import MigrationStatus

from helpers import FIXTURES_DIR, make_local_options


@pytest.mark.local
def test_second_run_skips_all(tmp_path):
    records1 = run_migration(FIXTURES_DIR, make_local_options(tmp_path))
    assert all(r.status == MigrationStatus.SUCCESS for r in records1)

    file_count_after_run1 = sum(1 for _ in tmp_path.rglob("*") if _.is_file())
    records2 = run_migration(FIXTURES_DIR, make_local_options(tmp_path))
    assert all(r.status == MigrationStatus.SKIPPED for r in records2), (
        f"Expected all SKIPPED on run 2, got: {[(r.title, r.status) for r in records2 if r.status != MigrationStatus.SKIPPED]}"
    )
    file_count_after_run2 = sum(1 for _ in tmp_path.rglob("*") if _.is_file())
    assert file_count_after_run1 == file_count_after_run2, (
        "File count changed between run 1 and run 2"
    )


@pytest.mark.local
def test_force_overrides_skip(tmp_path):
    run_migration(FIXTURES_DIR, make_local_options(tmp_path))
    records2 = run_migration(FIXTURES_DIR, make_local_options(tmp_path, force=True))
    assert all(r.status == MigrationStatus.SUCCESS for r in records2), (
        f"Expected all SUCCESS with --force, got: {[(r.title, r.status) for r in records2 if r.status != MigrationStatus.SUCCESS]}"
    )


@pytest.mark.local
def test_partial_resume(tmp_path):
    run_migration(FIXTURES_DIR, make_local_options(tmp_path))
    target = tmp_path / "Sanity Notebook" / "Text Only.docx"
    assert target.exists()
    target.unlink()

    records2 = run_migration(FIXTURES_DIR, make_local_options(tmp_path))
    successes = [r for r in records2 if r.status == MigrationStatus.SUCCESS]
    skipped = [r for r in records2 if r.status == MigrationStatus.SKIPPED]
    assert len(successes) == 1, f"Expected 1 re-migrated note, got: {[r.title for r in successes]}"
    assert successes[0].title == "Text Only"
    assert len(skipped) == len(records2) - 1
    assert target.exists(), "Text Only.docx was not re-created"


@pytest.mark.local
def test_duplicate_title_resume(tmp_path):
    run_migration(FIXTURES_DIR, make_local_options(tmp_path))
    nb = tmp_path / "Sanity Notebook"
    # Delete the (2) variant files
    for f in nb.glob("Text With Mixed Attachments (2)*"):
        f.unlink()

    records2 = run_migration(FIXTURES_DIR, make_local_options(tmp_path))

    # The duplicate note is recorded with the original title "Text With Mixed Attachments"
    # (the second occurrence). Find the one that was re-migrated.
    successes = [r for r in records2 if r.status == MigrationStatus.SUCCESS]
    assert len(successes) == 1, (
        f"Expected exactly 1 re-migrated note, got: {[(r.title, r.status) for r in successes]}"
    )
    assert list(nb.glob("Text With Mixed Attachments (2)*")), (
        "Duplicate (2) files were not re-created"
    )

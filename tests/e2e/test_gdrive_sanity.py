"""
GDrive e2e test: migration to Google Drive covers all note kinds and stacks.

Run with:  pytest -m gdrive -v
Run with cleanup:  pytest -m gdrive -v --cleanup-gdrive
Skipped automatically if OAuth credentials are not available.
"""

from pathlib import Path

import pytest

from evernote_to_gdrive.auth import token_path, get_services
from collections import Counter

from evernote_to_gdrive.drive_files import _list_folder_files_pairs, list_folder_files, list_folder_files_all
from evernote_to_gdrive.drive_folders import find_folder, find_folder_path
from evernote_to_gdrive.migrate import run_migration
from evernote_to_gdrive.models import (
    AttachmentPolicy,
    MigrationOptions,
    MigrationRecord,
    OutputMode,
)

from helpers import assert_status_all_success, docx_external_hyperlinks

FIXTURES_DIR = Path(__file__).parent.parent / "input" / "sanity"
DEST = "evernote-to-gdrive/tests/sanity"

pytestmark = pytest.mark.gdrive

_GDRIVE_SIBLING_GROUPS: dict[str, list[str]] = {
    "Multiple PDF Attachments_0": [
        "Multiple PDF Attachments_1.pdf",
        "Multiple PDF Attachments_2.pdf",
    ],
    "Mixed Attachments No Text_0": ["Mixed Attachments No Text_1.pdf"],
    "Text With PDF_0": ["Text With PDF_1.pdf"],
    "Text With Mixed Attachments_0": ["Text With Mixed Attachments_1.pdf"],
}

_EXPECTED_SANITY_FILES = {
    "Text Only",
    "Single Image Attachment.png",
    "Single PDF Attachment.pdf",
    "Multiple Image Attachments",
    "Multiple PDF Attachments_0",
    "Multiple PDF Attachments_1.pdf",
    "Multiple PDF Attachments_2.pdf",
    "Mixed Attachments No Text_0",
    "Mixed Attachments No Text_1.pdf",
    "Text With Image",
    "Text With PDF_0",
    "Text With PDF_1.pdf",
    "Text With Mixed Attachments_0",
    "Text With Mixed Attachments_1.pdf",
    "Text Only With Tags",
    "Inter-Note Link Target",
    "Inter-Note Link Source",
}


@pytest.fixture(scope="module")
def gdrive_migration(request, gdrive_secrets_dir: Path):
    if not token_path(gdrive_secrets_dir).exists():
        pytest.skip("Google Drive credentials not available (no token.json)")

    print(f"\n>>> GDrive output → {DEST}")
    drive = get_services(secrets_folder=gdrive_secrets_dir)

    # Clean up any previous test run (mirrors local sanity's shutil.rmtree)
    eg_id = find_folder(drive, "evernote-to-gdrive")
    if eg_id:
        tests_id = find_folder(drive, "tests", parent_id=eg_id)
        if tests_id:
            sanity_id = find_folder(drive, "sanity", parent_id=tests_id)
            if sanity_id:
                drive.files().delete(fileId=sanity_id).execute()

    options = MigrationOptions(
        output_mode=OutputMode.GOOGLE,
        dest=DEST,
        notebooks=[],
        stacks=[],
        note=None,
        attachments=AttachmentPolicy.DOC,
        log_file=None,
        secrets_folder=gdrive_secrets_dir,
        verbose=False,
    )
    records = run_migration(FIXTURES_DIR, options)

    yield drive, records

    if request.config.getoption("--cleanup-gdrive"):
        eg_id = find_folder(drive, "evernote-to-gdrive")
        if eg_id:
            tests_id = find_folder(drive, "tests", parent_id=eg_id)
            if tests_id:
                sanity_id = find_folder(drive, "sanity", parent_id=tests_id)
                if sanity_id:
                    drive.files().delete(fileId=sanity_id).execute()


# ── helpers ──────────────────────────────────────────────────────────────────

def _export_gdoc_as_docx(drive, file_id: str) -> Path:
    import io
    import tempfile
    from googleapiclient.http import MediaIoBaseDownload
    req = drive.files().export_media(
        fileId=file_id,
        mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    tmp = Path(tempfile.mkstemp(suffix=".docx")[1])
    tmp.write_bytes(buf.getvalue())
    return tmp


# ── test ─────────────────────────────────────────────────────────────────────

def _assert_record_status(records: list[MigrationRecord]) -> None:
    assert len(records) == 14, f"Expected 14 records, got {len(records)}"
    assert_status_all_success(records)
    empty = [r for r in records if not r.output]
    assert not empty, f"Records with no output IDs: {[r.title for r in empty]}"


def _assert_sanity_files(drive, nb_id: str) -> None:
    files = list_folder_files(drive, nb_id)
    assert files == _EXPECTED_SANITY_FILES, (
        f"Unexpected files in Sanity Notebook.\n"
        f"  Missing: {_EXPECTED_SANITY_FILES - files}\n"
        f"  Extra:   {files - _EXPECTED_SANITY_FILES}"
    )


def _assert_sibling_links(drive, nb_id: str) -> None:
    all_pairs = _list_folder_files_pairs(drive, nb_id)
    files_with_ids = dict(all_pairs)
    for doc_name, expected_siblings in _GDRIVE_SIBLING_GROUPS.items():
        doc_id = files_with_ids[doc_name]
        # Use all pairs so duplicate filenames don't hide a valid sibling id
        expected_ids = {fid for name, fid in all_pairs if name in expected_siblings}
        tmp = _export_gdoc_as_docx(drive, doc_id)
        try:
            targets = docx_external_hyperlinks(tmp)
        finally:
            tmp.unlink(missing_ok=True)
        # At least one sibling id must appear (duplicate notes produce duplicate sibling files)
        found = [fid for fid in expected_ids if any(fid in t for t in targets)]
        assert found, (
            f"{doc_name} (Drive) has no hyperlinks to any sibling file id\n"
            f"  expected any of: {expected_ids}\n"
            f"  found targets: {targets}"
        )


def _assert_interlink(drive, nb_id: str) -> None:
    files_with_ids = dict(_list_folder_files_pairs(drive, nb_id))
    source_id = files_with_ids["Inter-Note Link Source"]
    target_id = files_with_ids["Inter-Note Link Target"]
    tmp = _export_gdoc_as_docx(drive, source_id)
    try:
        interlink_targets = docx_external_hyperlinks(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    assert any(target_id in t for t in interlink_targets), (
        f"Inter-Note Link Source missing hyperlink to Inter-Note Link Target ({target_id})\n"
        f"  found targets: {interlink_targets}"
    )


def _assert_duplicates(drive, nb_id: str) -> None:
    all_files = list_folder_files_all(drive, nb_id)
    assert len(all_files) == 19, f"Expected 19 total files (including duplicates), got {len(all_files)}"
    counts = Counter(all_files)
    assert counts["Text With Mixed Attachments_0"] == 2, (
        f"Expected 2 copies of 'Text With Mixed Attachments_0', got {counts['Text With Mixed Attachments_0']}"
    )
    assert counts["Text With Mixed Attachments_1.pdf"] == 2, (
        f"Expected 2 copies of 'Text With Mixed Attachments_1.pdf', got {counts['Text With Mixed Attachments_1.pdf']}"
    )


def _assert_stack_structure(drive) -> None:
    stack_nb_id = find_folder_path(drive, DEST, "Stacked Notebook", stack="Test Stack")
    assert stack_nb_id is not None, "Stacked Notebook folder not found under Test Stack on Drive"
    stack_files = list_folder_files(drive, stack_nb_id)
    assert "Note In Stack" in stack_files, f"Expected 'Note In Stack' in {stack_files}"


@pytest.mark.sanity
@pytest.mark.gdrive
def test_record_count(gdrive_migration: tuple) -> None:
    _, records = gdrive_migration
    _assert_record_status(records)


@pytest.mark.sanity
@pytest.mark.gdrive
def test_output_files_exist(gdrive_migration: tuple) -> None:
    drive, _ = gdrive_migration
    nb_id = find_folder_path(drive, DEST, "Sanity Notebook")
    assert nb_id is not None, "Sanity Notebook folder not found on Drive"
    _assert_sanity_files(drive, nb_id)


@pytest.mark.sanity
@pytest.mark.gdrive
def test_sibling_links(gdrive_migration: tuple) -> None:
    drive, _ = gdrive_migration
    nb_id = find_folder_path(drive, DEST, "Sanity Notebook")
    assert nb_id is not None, "Sanity Notebook folder not found on Drive"
    _assert_sibling_links(drive, nb_id)


@pytest.mark.sanity
@pytest.mark.gdrive
def test_interlink(gdrive_migration: tuple) -> None:
    drive, _ = gdrive_migration
    nb_id = find_folder_path(drive, DEST, "Sanity Notebook")
    assert nb_id is not None, "Sanity Notebook folder not found on Drive"
    _assert_interlink(drive, nb_id)


@pytest.mark.sanity
@pytest.mark.gdrive
def test_duplicates(gdrive_migration: tuple) -> None:
    drive, _ = gdrive_migration
    nb_id = find_folder_path(drive, DEST, "Sanity Notebook")
    assert nb_id is not None, "Sanity Notebook folder not found on Drive"
    _assert_duplicates(drive, nb_id)


@pytest.mark.sanity
@pytest.mark.gdrive
def test_stack_structure(gdrive_migration: tuple) -> None:
    drive, _ = gdrive_migration
    _assert_stack_structure(drive)

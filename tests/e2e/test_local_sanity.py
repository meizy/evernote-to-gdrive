"""
Sanity e2e test: local migration covers all note kinds, tags, stacks, and formatting.

A single session-scoped migration run is shared across all assertions.
"""

from pathlib import Path

import pytest
from docx import Document

from evernote_to_gdrive.models import MigrationRecord

from helpers import assert_status_all_success, docx_text, docx_external_hyperlinks


# ── constants ─────────────────────────────────────────────────────────────────

_LOCAL_SIBLING_GROUPS: dict[str, list[str]] = {
    "Multiple PDF Attachments_0.docx": [
        "Multiple PDF Attachments_1.pdf",
        "Multiple PDF Attachments_2.pdf",
    ],
    "Mixed Attachments No Text_0.docx": ["Mixed Attachments No Text_1.pdf"],
    "Text With PDF_0.docx": ["Text With PDF_1.pdf"],
    "Text With Mixed Attachments_0.docx": ["Text With Mixed Attachments_1.pdf"],
    "Text With Mixed Attachments (2)_0.docx": ["Text With Mixed Attachments (2)_1.pdf"],
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _file(base: Path, *parts: str) -> Path:
    return base.joinpath(*parts)


def _assert_exists(base: Path, *parts: str) -> None:
    p = _file(base, *parts)
    assert p.exists(), f"Expected file not found: {p}"


def _assert_sanity_files(out: Path) -> None:
    nb = out / "Sanity Notebook"
    _assert_exists(nb, "Text Only.docx")
    _assert_exists(nb, "Single Image Attachment.png")
    _assert_exists(nb, "Single PDF Attachment.pdf")
    # multi-image: images embedded, no _0 suffix
    _assert_exists(nb, "Multiple Image Attachments.docx")
    # multi-pdf: _0 doc + sibling PDFs
    _assert_exists(nb, "Multiple PDF Attachments_0.docx")
    _assert_exists(nb, "Multiple PDF Attachments_1.pdf")
    _assert_exists(nb, "Multiple PDF Attachments_2.pdf")
    # mixed no text: image embedded, PDF sibling
    _assert_exists(nb, "Mixed Attachments No Text_0.docx")
    _assert_exists(nb, "Mixed Attachments No Text_1.pdf")
    # text + image: image embedded, no sibling
    _assert_exists(nb, "Text With Image.docx")
    # text + pdf: _0 doc + sibling PDF
    _assert_exists(nb, "Text With PDF_0.docx")
    _assert_exists(nb, "Text With PDF_1.pdf")
    # text + mixed: image embedded, PDF sibling
    _assert_exists(nb, "Text With Mixed Attachments_0.docx")
    _assert_exists(nb, "Text With Mixed Attachments_1.pdf")
    # duplicate of text+mixed: renamed with (2) suffix
    _assert_exists(nb, "Text With Mixed Attachments (2)_0.docx")
    _assert_exists(nb, "Text With Mixed Attachments (2)_1.pdf")
    # tags note
    _assert_exists(nb, "Text Only With Tags.docx")
    # inter-note link notes
    _assert_exists(nb, "Inter-Note Link Target.docx")
    _assert_exists(nb, "Inter-Note Link Source.docx")


def _assert_tags_in_docx(out: Path) -> None:
    docx_path = out / "Sanity Notebook" / "Text Only With Tags.docx"
    text = docx_text(docx_path)
    assert "tag:tag1" in text, f"tag1 not found in docx text:\n{text}"
    assert "tag:tag2" in text, f"tag2 not found in docx text:\n{text}"


def _assert_text_only_formatting(out: Path) -> None:
    doc = Document(str(out / "Sanity Notebook" / "Text Only.docx"))
    paragraphs = doc.paragraphs

    heading_paras = [p for p in paragraphs if "heading" in p.style.name.lower()]
    assert heading_paras, "No heading paragraph found in Text Only.docx"
    assert any("Heading One" in p.text for p in heading_paras), (
        f"'Heading One' not found in heading paragraphs: {[p.text for p in heading_paras]}"
    )

    all_text = [p.text for p in paragraphs]
    assert any("Normal paragraph" in t for t in all_text), (
        f"'Normal paragraph' not found in doc: {all_text}"
    )

    bold_runs = [r for p in paragraphs for r in p.runs if r.bold and r.text.strip()]
    assert any("Bold text" in r.text for r in bold_runs), (
        f"No bold run with 'Bold text' found"
    )

    italic_runs = [r for p in paragraphs for r in p.runs if r.italic and r.text.strip()]
    assert any("italic text" in r.text for r in italic_runs), (
        f"No italic run with 'italic text' found"
    )


def _assert_sibling_links(out: Path) -> None:
    nb = out / "Sanity Notebook"
    for doc_name, expected in _LOCAL_SIBLING_GROUPS.items():
        targets = docx_external_hyperlinks(nb / doc_name)
        missing = [s for s in expected if s not in targets]
        assert not missing, (
            f"{doc_name} is missing hyperlinks to siblings: {missing}\n"
            f"  found targets: {targets}"
        )


def _assert_interlink(out: Path) -> None:
    source = out / "Sanity Notebook" / "Inter-Note Link Source.docx"
    targets = docx_external_hyperlinks(source)
    assert any("Inter-Note%20Link%20Target.docx" in t for t in targets), (
        f"Inter-Note Link Source.docx missing hyperlink to Inter-Note Link Target.docx\n"
        f"  found targets: {targets}"
    )


def _assert_stack_structure(out: Path) -> None:
    _assert_exists(out, "Test Stack", "Stacked Notebook", "Note In Stack.docx")


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.sanity
@pytest.mark.local
def test_record_count(migrated_output: tuple[Path, list[MigrationRecord]]) -> None:
    _, records = migrated_output
    assert len(records) == 14, f"Expected 14 records, got {len(records)}"
    assert_status_all_success(records)


@pytest.mark.sanity
@pytest.mark.local
def test_output_files_exist(migrated_output: tuple[Path, list[MigrationRecord]]) -> None:
    out, _ = migrated_output
    _assert_sanity_files(out)


@pytest.mark.sanity
@pytest.mark.local
def test_sibling_links(migrated_output: tuple[Path, list[MigrationRecord]]) -> None:
    out, _ = migrated_output
    _assert_sibling_links(out)


@pytest.mark.sanity
@pytest.mark.local
def test_tags_in_docx(migrated_output: tuple[Path, list[MigrationRecord]]) -> None:
    out, _ = migrated_output
    _assert_tags_in_docx(out)


@pytest.mark.sanity
@pytest.mark.local
def test_text_only_formatting(migrated_output: tuple[Path, list[MigrationRecord]]) -> None:
    out, _ = migrated_output
    _assert_text_only_formatting(out)


@pytest.mark.sanity
@pytest.mark.local
def test_stack_structure(migrated_output: tuple[Path, list[MigrationRecord]]) -> None:
    out, _ = migrated_output
    _assert_stack_structure(out)


@pytest.mark.sanity
@pytest.mark.local
def test_interlink(migrated_output: tuple[Path, list[MigrationRecord]]) -> None:
    out, _ = migrated_output
    _assert_interlink(out)

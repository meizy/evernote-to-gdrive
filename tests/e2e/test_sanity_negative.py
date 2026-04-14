"""
Negative tests: verify that sanity assertion helpers reject bad input.

These tests do NOT depend on the migration fixture -- they use the static
plain.docx asset and tmp_path to exercise each assertion in isolation.
"""

import shutil
from pathlib import Path

import pytest

from test_local_sanity import (
    _LOCAL_SIBLING_GROUPS,
    _assert_exists,
    _assert_sibling_links,
    _assert_stack_structure,
    _assert_tags_in_docx,
    _assert_text_only_formatting,
)

PLAIN_DOCX = Path(__file__).parent.parent / "assets" / "plain.docx"


def _stage(tmp_path: Path, *parts: str) -> Path:
    dest = tmp_path.joinpath(*parts)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PLAIN_DOCX, dest)
    return dest


@pytest.mark.local
def test_negative_assert_exists_nonexistent(tmp_path: Path) -> None:
    with pytest.raises(AssertionError):
        _assert_exists(tmp_path, "does_not_exist.docx")


@pytest.mark.local
def test_negative_formatting(tmp_path: Path) -> None:
    _stage(tmp_path, "Sanity Notebook", "Text Only.docx")
    with pytest.raises(AssertionError):
        _assert_text_only_formatting(tmp_path)


@pytest.mark.local
def test_negative_tags(tmp_path: Path) -> None:
    _stage(tmp_path, "Sanity Notebook", "Text Only With Tags.docx")
    with pytest.raises(AssertionError):
        _assert_tags_in_docx(tmp_path)


@pytest.mark.local
def test_negative_sibling_links(tmp_path: Path) -> None:
    for doc_name in _LOCAL_SIBLING_GROUPS:
        _stage(tmp_path, "Sanity Notebook", doc_name)
    with pytest.raises(AssertionError):
        _assert_sibling_links(tmp_path)


@pytest.mark.local
def test_negative_stack_structure(tmp_path: Path) -> None:
    with pytest.raises(AssertionError):
        _assert_stack_structure(tmp_path)

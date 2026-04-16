"""
CLI error handling e2e tests.

Use click's CliRunner to verify error exits and messages for invalid usage.
"""

import pytest
from click.testing import CliRunner

from evernote_to_gdrive.cli import main

from helpers import FIXTURES_DIR


@pytest.mark.local
def test_note_without_notebook():
    runner = CliRunner()
    result = runner.invoke(main, [
        "migrate", str(FIXTURES_DIR),
        "--output", "local",
        "--dest", "/tmp/unused",
        "--note", "Some Note",
    ])
    assert result.exit_code != 0
    assert "--notebook" in result.output


@pytest.mark.local
def test_invalid_flag():
    runner = CliRunner()
    result = runner.invoke(main, [
        "migrate", str(FIXTURES_DIR),
        "--bogus-flag",
    ])
    assert result.exit_code != 0
    assert "No such option" in result.output

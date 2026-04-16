"""
Auth command e2e tests.

Run the real auth CLI command and verify it creates a real token.json when
the secrets folder starts empty.
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

from evernote_to_gdrive import auth as auth_mod
from evernote_to_gdrive.cli import main


@pytest.mark.auth
def test_auth_command_creates_token_in_requested_folder(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "auth-store"
    if auth_mod._bundled_client_secrets_text() is None and auth_mod._repo_client_secrets_text() is None:
        pytest.skip("Google OAuth client secrets are not available for auth")

    runner = CliRunner()
    result = runner.invoke(main, ["auth", "--secrets-folder", str(secrets_dir)])
    normalized_output = result.output.replace("\n", "")

    assert result.exit_code == 0, f"auth command failed:\n{result.output}"
    assert (secrets_dir / "token.json").exists()
    assert "Authenticated." in result.output
    assert "Token saved to:" in result.output
    assert str(secrets_dir / "token.json") in normalized_output

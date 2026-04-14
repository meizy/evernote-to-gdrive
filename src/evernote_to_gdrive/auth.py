"""
Google OAuth 2.0 authentication for the Drive API.

On first run the user is sent through a browser-based consent flow.
The resulting token is cached in the secrets folder and refreshed
automatically on subsequent runs.

Default secrets folder: the current working directory.

Override the folder with --secrets-folder when calling `auth` or `migrate`.

The caller must place their OAuth client secrets (client_secrets.json) in
the secrets folder (downloaded from Google Cloud Console → APIs & Services →
Credentials → OAuth 2.0 Client IDs → Desktop application).

The Google account that will run the migration must also be added as a test
user in the OAuth consent screen (APIs & Services → OAuth consent screen →
Test users) while the app remains in 'Testing' publishing status.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import google.auth.exceptions
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

_log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
]

_SECRETS_FILE = "client_secrets.json"
_TOKEN_FILE = "token.json"


def client_secrets_path(secrets_dir: Path) -> Path:
    return secrets_dir / _SECRETS_FILE


def token_path(secrets_dir: Path) -> Path:
    return secrets_dir / _TOKEN_FILE


def resolve_secrets_dir(override: Path | None) -> Path:
    """Return the secrets directory: explicit override, otherwise current working directory."""
    if override is not None:
        return override
    return Path.cwd()


def _load_or_refresh_credentials(secrets_dir: Path) -> Credentials:
    token_file = token_path(secrets_dir)
    secrets_file = client_secrets_path(secrets_dir)

    creds: Credentials | None = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, secrets_dir)
            return creds
        except google.auth.exceptions.RefreshError:
            _log.warning("Token expired or revoked — re-authenticating")
            token_file.unlink(missing_ok=True)

    # Need fresh authorization
    if not secrets_file.exists():
        print(
            f"\nError: OAuth client secrets file not found at:\n  {secrets_file}\n\n"
            "To set up authentication:\n"
            "  1. Go to https://console.cloud.google.com/\n"
            "  2. Create a project and enable the Drive API\n"
            "  3. Create OAuth 2.0 credentials (Desktop application)\n"
            f"  4. Download and save as: {secrets_file}\n"
            "  5. In the OAuth consent screen, add your Google account as a test user\n"
            "     (APIs & Services → OAuth consent screen → Test users)\n"
            "     This is required while the app is in 'Testing' publishing status.\n"
            "\nBy default the tool looks in the current working directory. "
            "Use --secrets-folder to point to a different location.\n",
            file=sys.stderr,
        )
        raise SystemExit(1)

    secrets_dir.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_file), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    _save_token(creds, secrets_dir)
    return creds


def _save_token(creds: Credentials, secrets_dir: Path) -> None:
    secrets_dir.mkdir(parents=True, exist_ok=True)
    token_path(secrets_dir).write_text(creds.to_json(), encoding="utf-8")


def get_services(secrets_folder: Path | None = None):
    """Return an authenticated Drive API client."""
    secrets_dir = resolve_secrets_dir(secrets_folder)
    creds = _load_or_refresh_credentials(secrets_dir)
    drive = build("drive", "v3", credentials=creds)
    _log.debug("authenticated successfully")
    return drive

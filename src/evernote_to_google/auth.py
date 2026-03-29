"""
Google OAuth 2.0 authentication for Drive and Docs APIs.

On first run the user is sent through a browser-based consent flow.
The resulting token is cached at ~/.config/evernote-to-google/token.json
and refreshed automatically on subsequent runs.

The caller must place their OAuth client secrets at:
  .config/client_secrets.json  (relative to the project root)
(downloaded from Google Cloud Console → APIs & Services → Credentials)

The Google account that will run the migration must also be added as a test user
in the OAuth consent screen (APIs & Services → OAuth consent screen → Test users)
while the app remains in 'Testing' publishing status.
"""

from __future__ import annotations

import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
]

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / ".config"
CLIENT_SECRETS = CONFIG_DIR / "client_secrets.json"
TOKEN_FILE = CONFIG_DIR / "token.json"


def _load_or_refresh_credentials() -> Credentials:
    creds: Credentials | None = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds)
        return creds

    # Need fresh authorization
    if not CLIENT_SECRETS.exists():
        print(
            f"\nError: OAuth client secrets file not found at:\n  {CLIENT_SECRETS}\n\n"
            "To set up authentication:\n"
            "  1. Go to https://console.cloud.google.com/\n"
            "  2. Create a project and enable the Drive API and Docs API\n"
            "  3. Create OAuth 2.0 credentials (Desktop application)\n"
            f"  4. Download and save as: {CLIENT_SECRETS}\n"
            "  5. In the OAuth consent screen, add your Google account as a test user\n"
            "     (APIs & Services → OAuth consent screen → Test users)\n"
            "     This is required while the app is in 'Testing' publishing status.\n",
            file=sys.stderr,
        )
        raise SystemExit(1)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    _save_token(creds)
    return creds


def _save_token(creds: Credentials) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())


def get_services() -> tuple:
    """Return (drive_service, docs_service) authenticated API clients."""
    creds = _load_or_refresh_credentials()
    drive = build("drive", "v3", credentials=creds)
    docs = build("docs", "v1", credentials=creds)
    return drive, docs

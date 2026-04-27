"""
Google OAuth 2.0 authentication for the Drive API.

On first run the user is sent through a browser-based consent flow.
The resulting token is cached in the secrets folder and refreshed
automatically on subsequent runs.

Default secrets folder: the current working directory.

Override the folder with --secrets-folder when calling `auth` or `migrate`.

If client_secrets.json is not present in the secrets folder, the package will
use a bundled project client when one was included in the build and copy it
into the secrets folder on first use. In source checkouts, a repo-local
.auth/client_secrets.json is also accepted as the source of truth.
"""

from __future__ import annotations

import json
import logging
from importlib import resources
from pathlib import Path

import google.auth.exceptions
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from ._runtime_paths import repo_root_or_none

_log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
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
    secrets_file = _ensure_client_secrets(secrets_dir)

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
    if secrets_file is None:
        _log.error(
            "OAuth client secrets file not found.\n"
            "Looked for:\n  %s\n"
            "and no bundled project client was included in this build.\n"
            "If this release was meant to be self-contained, rebuild it with "
            ".auth/client_secrets.json available at build time.\n"
            "Otherwise, follow the manual setup guide:\n"
            "  docs/google-credentials-setup.md\n"
            "Use --secrets-folder to point to a different location.",
            client_secrets_path(secrets_dir),
        )
        raise SystemExit(1)

    secrets_dir.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_file), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    _save_token(creds, secrets_dir)
    return creds


def _bundled_client_secrets_text() -> str | None:
    try:
        return resources.files("evernote_to_gdrive._bundled_auth").joinpath(_SECRETS_FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except ModuleNotFoundError:
        return None


def _repo_client_secrets_text() -> str | None:
    repo_root = repo_root_or_none()
    if repo_root is None:
        return None
    source = repo_root / ".auth" / _SECRETS_FILE
    if not source.exists():
        return None
    return source.read_text(encoding="utf-8")


def _ensure_client_secrets(secrets_dir: Path) -> Path | None:
    secrets_file = client_secrets_path(secrets_dir)
    if secrets_file.exists():
        return secrets_file

    bundled = _bundled_client_secrets_text()
    if bundled is None:
        bundled = _repo_client_secrets_text()
    if bundled is None:
        return None

    # Validate before writing so release packaging mistakes fail clearly.
    json.loads(bundled)
    secrets_dir.mkdir(parents=True, exist_ok=True)
    secrets_file.write_text(bundled, encoding="utf-8")
    _log.info("Wrote bundled OAuth client to %s", secrets_file)
    return secrets_file


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

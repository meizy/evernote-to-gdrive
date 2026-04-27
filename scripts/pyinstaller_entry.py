"""PyInstaller entry shim — PyInstaller needs a plain script, not a console-script."""

import os
from pathlib import Path
import certifi

# Fix SSL cert verification in the frozen binary (system CA store is unreachable on macOS).
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

# Pin Playwright browsers to a stable user-owned directory so that browsers
# installed via `install-browsers` survive across runs (PyInstaller extracts to
# a new temp dir each launch, so the default relative path changes every time).
os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH",
    str(Path.cwd() / ".cache" / "playwright-browsers"),
)

from evernote_to_gdrive.cli import main

if __name__ == "__main__":
    main()

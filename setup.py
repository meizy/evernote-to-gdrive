from pathlib import Path
import json
import os

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.sdist import sdist as _sdist

ROOT = Path(__file__).resolve().parent
SOURCE_SECRETS = ROOT / ".auth" / "client_secrets.json"
TARGET_DIR = ROOT / "src" / "evernote_to_gdrive" / "_bundled_auth"
TARGET_SECRETS = TARGET_DIR / "client_secrets.json"
ENV_SECRETS = "GOOGLE_CLIENT_SECRETS_JSON"


def _sync_bundled_auth() -> None:
    source_text: str | None = None
    if SOURCE_SECRETS.exists():
        source_text = SOURCE_SECRETS.read_text(encoding="utf-8")
    elif os.environ.get(ENV_SECRETS):
        source_text = os.environ[ENV_SECRETS]

    if source_text is None:
        return

    json.loads(source_text)
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    TARGET_SECRETS.write_text(source_text, encoding="utf-8")


class build_py(_build_py):
    def run(self):
        _sync_bundled_auth()
        super().run()


class sdist(_sdist):
    def run(self):
        _sync_bundled_auth()
        super().run()


setup(cmdclass={"build_py": build_py, "sdist": sdist})

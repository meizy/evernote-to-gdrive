"""
Shared pytest fixtures for e2e tests.

ENEX fixtures are static files in tests/input/ — generated once via
generate_fixtures.py and committed to the repo. Do not regenerate them here.
"""

import shutil
from pathlib import Path

import pytest

from evernote_to_gdrive.auth import resolve_secrets_dir
from evernote_to_gdrive.migrate import run_migration
from evernote_to_gdrive.models import MigrationRecord

from helpers import FIXTURES_DIR, make_local_options


# Modules that own a module-level Rich `console` used for progress bars and output.
# All of them must be patched together so every Rich call goes to the same Console
# instance (Rich Progress bars break if created on one Console but rendered on another).
_CONSOLE_USERS = [
    "evernote_to_gdrive._console",
    "evernote_to_gdrive.analyze_reports",
    "evernote_to_gdrive.cli",
    "evernote_to_gdrive.migrate",
    "evernote_to_gdrive.analyze_links",
]


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("gdrive"):
        import importlib
        from rich.console import Console

        # gdrive tests upload to Google Drive and can take 30+ seconds.
        # pytest captures stdout/stderr by default, which suppresses Rich's progress
        # bar entirely — the terminal appears frozen with no feedback.
        # Suspending capture lets Rich write directly to the real TTY so the
        # progress bar is visible during the test run.
        item.config.pluginmanager.get_plugin("capturemanager").suspend_global_capture(in_=True)

        # Replace every module-level console with a fresh one bound to the live TTY.
        # We save the originals so teardown can restore them; without restoration,
        # any non-gdrive test that runs afterward would inherit this live console
        # and write outside pytest's capture, making suite order matter.
        live_console = Console()
        originals: dict[str, object] = {}
        for mod_name in _CONSOLE_USERS:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, "console"):
                originals[mod_name] = mod.console
                mod.console = live_console
        item._gdrive_console_originals = originals  # type: ignore[attr-defined]


def pytest_runtest_teardown(item: pytest.Item) -> None:
    if item.get_closest_marker("gdrive"):
        import importlib

        # Restore the original console objects before resuming capture so later
        # tests get the normal pytest-captured console, not the live-TTY one.
        originals: dict[str, object] = getattr(item, "_gdrive_console_originals", {})
        for mod_name, original in originals.items():
            mod = importlib.import_module(mod_name)
            mod.console = original
        item.config.pluginmanager.get_plugin("capturemanager").resume_global_capture()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--cleanup-gdrive",
        action="store_true",
        default=False,
        help="Delete uploaded files from Google Drive after gdrive tests.",
    )


# Fixed path by design: output persists after the test run for manual inspection.
# Do not replace with tmp_path — the stable location is intentional.
OUTPUT_DIR = Path(__file__).parent.parent / "output" / "sanity"


@pytest.fixture(scope="session")
def gdrive_secrets_dir() -> Path:
    """Temporary test-only location for Google OAuth files."""
    return resolve_secrets_dir(Path(".auth"))


@pytest.fixture(scope="session")
def migrated_output() -> tuple[Path, list[MigrationRecord]]:
    """Run a full local migration into tests/output/sanity and return (dest, records).

    The output directory is a fixed repo path (not a temp dir) so the migrated
    files remain on disk after the test run for manual inspection.
    """
    print(f"\n>>> Sanity output → {OUTPUT_DIR}")
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)
    records = run_migration(FIXTURES_DIR, make_local_options(OUTPUT_DIR))
    return OUTPUT_DIR, records

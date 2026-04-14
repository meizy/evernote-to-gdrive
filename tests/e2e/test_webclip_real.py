"""
Real webclip e2e tests: full Playwright + Readability pipeline.

Run with:  pytest -m webclip
Skipped automatically if playwright is not installed.
"""

from pathlib import Path

import pytest

from evernote_to_gdrive.migrate import run_migration
from evernote_to_gdrive.models import MigrationRecord, MigrationStatus

from helpers import make_local_options

FIXTURES_DIR = Path(__file__).parent.parent / "input" / "webclip"
# Fixed path by design: output persists after the test run for manual inspection.
# Do not replace with tmp_path — the stable location is intentional.
OUTPUT_DIR = Path(__file__).parent.parent / "output" / "webclip"

pytestmark = [pytest.mark.webclip, pytest.mark.local]


@pytest.fixture(scope="module")
def webclip_output() -> tuple[Path, list[MigrationRecord]]:
    """Run webclip migration into tests/output/webclip and return (dest, records).

    The output directory is a fixed repo path (not a temp dir) so the generated
    PDFs remain on disk after the test run for manual inspection.
    """
    pytest.importorskip("playwright", reason="playwright not installed — skipping webclip tests")
    print(f"\n>>> Webclip output → {OUTPUT_DIR}")
    import shutil
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir()
    records = run_migration(FIXTURES_DIR / "Web Clips.enex", make_local_options(OUTPUT_DIR))
    return OUTPUT_DIR, records


def test_webclip_real(webclip_output: tuple[Path, list[MigrationRecord]]) -> None:
    out, records = webclip_output
    nb = out / "Web Clips"

    assert len(records) == 2, f"Expected 2 records, got {len(records)}"

    failed = [r for r in records if r.status != MigrationStatus.SUCCESS]
    assert not failed, "\n".join(f"  {r.title}: {r.status} {r.error}" for r in failed)

    for name in ("Web Clip Plain.pdf", "Web Clip With Image.pdf"):
        p = nb / name
        assert p.exists(), f"Expected PDF not found: {p}"
        assert p.stat().st_size > 1000, f"PDF suspiciously small: {p}"

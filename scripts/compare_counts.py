"""
Compare Evernote analyze output against the actual Drive folder tree.

Reconciles two CSVs:
  --drive                flat drive tree (from scripts/list_drive_tree.py)
  --analyze-attachments  notes_with_attachments.csv (from analyze --list-attachments
                         --include-zero --write-csv) — one row per note

For each notebook it computes an *expected* direct-file count from the ENEX
data plus the --attachments policy (mirroring the migration's dispatch logic)
and compares it to the actual count on Drive.

Output CSV columns: notebook, analyze_count, drive_count, expected_drive_count.

Hebrew/RTL note: analyze CSVs rendered on non-BiDi terminals (e.g. VSCode) have
physically-reversed Hebrew. This script strips BiDi controls and falls back to
per-segment reversed matching — but running analyze with --bidi true gives the
cleanest match.

Usage:
    python scripts/compare_counts.py --attachments doc
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import click

from evernote_to_gdrive.classifier import _is_rtl, safe_drive_name

_FOLDER_MIME = "application/vnd.google-apps.folder"
_BIDI_CONTROLS = {"\u200e", "\u200f", "\u2066", "\u2067", "\u2068", "\u2069"}


def _strip_bidi(s: str) -> str:
    return "".join(c for c in s if c not in _BIDI_CONTROLS)


def _canonical(key: str) -> str:
    parts = [safe_drive_name(p) for p in _strip_bidi(key).split("/") if p]
    return "/".join(parts)


def _canonical_rtl_reversed(key: str) -> str:
    parts = _strip_bidi(key).split("/")
    reversed_parts = [safe_drive_name(p[::-1] if _is_rtl(p) else p) for p in parts if p]
    return "/".join(reversed_parts)


def _load_drive_flat(path: Path) -> tuple[dict[str, int], set[str]]:
    """Return (files_by_parent_path, all_folder_paths). Paths are root-relative."""
    files_by_parent: dict[str, int] = defaultdict(int)
    folder_paths: set[str] = set()
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            parent = row["path"]
            name = row["name"]
            mime = row["mime"]
            if mime == _FOLDER_MIME:
                full = f"{parent}/{name}" if parent else name
                folder_paths.add(full)
            else:
                files_by_parent[parent] += 1
    return dict(files_by_parent), folder_paths


def _leaf_folders(folder_paths: set[str]) -> set[str]:
    """Folders with no sub-folders (i.e., notebook folders in the migration layout)."""
    parents = {p.rsplit("/", 1)[0] for p in folder_paths if "/" in p}
    return {f for f in folder_paths if f not in parents}


def _predict_per_note(has_text: bool, images: int, pdfs: int, other: int, policy: str) -> int:
    non_image = pdfs + other
    total = images + pdfs + other
    if has_text:
        return 1 + non_image
    if total == 1:
        return 1
    if policy == "files" and images == 0:
        return total
    return 1 + non_image


def _load_analyze_attachments(path: Path, policy: str) -> tuple[dict[str, int], dict[str, int]]:
    """Single pass over the per-note attachments CSV.

    Returns (notes_per_notebook, expected_drive_files_per_notebook).
    """
    counts: dict[str, int] = defaultdict(int)
    expected: dict[str, int] = defaultdict(int)
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            nb = row["Notebook"]
            has_text = row["Text"].strip().upper() == "Y"
            counts[nb] += 1
            expected[nb] += _predict_per_note(
                has_text, int(row["Images"]), int(row["PDFs"]), int(row["Other"]), policy,
            )
    return dict(counts), dict(expected)


def _build_unified(
    analyze: dict[str, int],
    expected: dict[str, int],
    drive_leaf_paths: set[str],
    drive_files: dict[str, int],
) -> list[tuple[str, str, str, str]]:
    """Join the three sources on a single canonical notebook key (RTL-aware).

    The drive side is treated as the truth for canonical form: analyze/expected
    keys are matched against drive paths both plainly and with RTL-reversed
    segments, so VSCode-rendered analyze CSVs still line up.
    """
    drive_by_canon = {_canonical(p): p for p in drive_leaf_paths}

    def resolve_canon(key: str) -> str:
        c = _canonical(key)
        if c in drive_by_canon:
            return c
        r = _canonical_rtl_reversed(key)
        if r in drive_by_canon:
            return r
        return c

    display: dict[str, str] = {}
    a_by_canon: dict[str, int] = {}
    e_by_canon: dict[str, int] = {}

    for k, v in analyze.items():
        c = resolve_canon(k)
        display.setdefault(c, k)
        a_by_canon[c] = v

    for k, v in expected.items():
        c = resolve_canon(k)
        display.setdefault(c, k)
        e_by_canon[c] = v

    for p in drive_leaf_paths:
        c = _canonical(p)
        display.setdefault(c, p)

    rows: list[tuple[str, str, str, str]] = []
    for canon in sorted(display):
        name = display[canon]
        a = a_by_canon.get(canon, "")
        e = e_by_canon.get(canon, "")
        drive_path = drive_by_canon.get(canon)
        d = drive_files.get(drive_path, 0) if drive_path is not None else ""
        rows.append((name, str(a), str(d), str(e)))
    return rows


def _write_csv(rows: list[tuple[str, str, str, str]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["notebook", "analyze_count", "drive_count", "expected_drive_count"])
        writer.writerows(rows)


def _print_summary(rows: list[tuple[str, str, str, str]]) -> None:
    matches = 0
    mismatches: list[tuple[str, str, str]] = []   # (name, drive, expected)
    analyze_only: list[str] = []
    drive_only: list[str] = []
    for name, a, d, e in rows:
        has_a = a != ""
        has_d = d != ""
        has_e = e != ""
        if has_d and has_e:
            if int(d) == int(e):
                matches += 1
            else:
                mismatches.append((name, d, e))
        elif has_a and not has_d:
            analyze_only.append(name)
        elif has_d and not has_a:
            drive_only.append(name)

    click.echo(f"\nCompared {len(rows)} notebooks.")
    click.echo(f"  Match         : {matches}   (drive_count == expected_drive_count)")
    click.echo(f"  Mismatch      : {len(mismatches)}")
    click.echo(f"  Analyze-only  : {len(analyze_only)}")
    click.echo(f"  Drive-only    : {len(drive_only)}")

    if mismatches:
        click.echo("\nMismatches (drive vs expected):")
        for name, d, e in mismatches:
            click.echo(f"  {name}  drive={d}  expected={e}")
    if analyze_only:
        click.echo("\nAnalyze-only (no matching drive folder):")
        for name in analyze_only:
            click.echo(f"  {name}")
    if drive_only:
        click.echo("\nDrive-only (no matching notebook in analyze):")
        for name in drive_only:
            click.echo(f"  {name}")


def _show_expected(atts_path: Path, policy: str, notebook: str) -> None:
    """Print all notes in a given notebook with the computed Expected per-note count."""
    target_plain = _canonical(notebook)
    target_reversed = _canonical(_canonical_rtl_reversed(notebook))

    with atts_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or []) + ["Expected"]
        matches: list[dict] = []
        for row in reader:
            nb = row["Notebook"]
            if _canonical(nb) in (target_plain, target_reversed) or \
               _canonical(_canonical_rtl_reversed(nb)) in (target_plain, target_reversed):
                has_text = row["Text"].strip().upper() == "Y"
                expected = _predict_per_note(
                    has_text, int(row["Images"]), int(row["PDFs"]), int(row["Other"]), policy,
                )
                matches.append({**row, "Expected": expected})

    if not matches:
        click.echo(f"No notes found for notebook '{notebook}'")
        return

    widths = {h: max(len(h), max(len(str(r[h])) for r in matches)) for h in headers}
    click.echo("  ".join(h.ljust(widths[h]) for h in headers))
    click.echo("  ".join("-" * widths[h] for h in headers))
    for row in matches:
        click.echo("  ".join(str(row[h]).ljust(widths[h]) for h in headers))
    click.echo(f"\n{len(matches)} note(s); Σ Expected = {sum(r['Expected'] for r in matches)}")


@click.command()
@click.option("--drive", "drive_path", default="output/drive_tree.csv",
              type=click.Path(dir_okay=False, path_type=Path),
              help="Flat drive tree CSV from scripts/list_drive_tree.py. Not required with --show-expected.")
@click.option("--analyze-attachments", "atts_path", default="output/analyze/notes_with_attachments.csv",
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="notes_with_attachments.csv from 'analyze --list-attachments --include-zero --write-csv'.")
@click.option("--attachments", "policy", type=click.Choice(["doc", "files"], case_sensitive=False),
              default="doc", show_default=True,
              help="Attachment policy used during migration. Mirrors the migrate CLI flag.")
@click.option("--output", "out_path", default="output/compare-counts.csv",
              type=click.Path(path_type=Path), help="Destination CSV path.")
@click.option("--show-expected", "show_expected", default=None, metavar="NOTEBOOK",
              help="Instead of running the comparison, print per-note breakdown "
                   "(with computed Expected) for the given notebook and exit.")
def main(drive_path: Path, atts_path: Path, policy: str, out_path: Path,
         show_expected: str | None) -> None:
    if show_expected:
        _show_expected(atts_path, policy, show_expected)
        return

    drive_files, folder_paths = _load_drive_flat(drive_path)
    analyze, expected = _load_analyze_attachments(atts_path, policy)

    rows = _build_unified(analyze, expected, _leaf_folders(folder_paths), drive_files)
    _write_csv(rows, out_path)
    click.echo(f"Wrote {len(rows)} rows to {out_path}")
    _print_summary(rows)


if __name__ == "__main__":
    main()

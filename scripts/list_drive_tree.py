"""
Recursively walk a Google Drive folder subtree and emit a flat list of every
folder and file with its root-relative parent path and MIME type.

NOTE: uses the drive.file OAuth scope — only sees folders/files this app
created (i.e., the Evernote migration output). Folders shared by others
or created via the Drive UI are invisible.

CSV schema (three columns):
    path, name, mime
- path: the containing folder path, root-relative. Empty string for root's
  direct children.
- name: the folder or file name.
- mime: Drive mimeType (folders: application/vnd.google-apps.folder,
  Google Docs: application/vnd.google-apps.document, etc.)

Usage:
    python scripts/list_drive_tree.py --root "Evernote Migration" --output output/drive-tree.csv
"""

from __future__ import annotations

import csv
import logging
import re
import sys
from collections import deque
from pathlib import Path

import click

from evernote_to_gdrive.auth import get_services
from evernote_to_gdrive.drive_folders import find_folder
from evernote_to_gdrive.drive_retry import _retry

_log = logging.getLogger(__name__)

_FOLDER_MIME = "application/vnd.google-apps.folder"


def _list_children(drive, parent_id: str) -> list[dict]:
    """Return all non-trashed children of parent_id with id, name, mimeType."""
    q = f"'{parent_id}' in parents and trashed = false"
    fields = "nextPageToken, files(id, name, mimeType)"
    result: list[dict] = []
    page_token: str | None = None
    while True:
        kwargs: dict = dict(q=q, fields=fields, spaces="drive", pageSize=1000)
        if page_token:
            kwargs["pageToken"] = page_token
        resp = _retry(
            drive.files().list(**kwargs).execute,
            op=f"list children of '{parent_id}'",
        )
        result.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return result


def _resolve_root(drive, path: str) -> str:
    """Walk path segments to find the root folder ID. Exits on failure."""
    parts = [p for p in re.split(r"[/\\]", path) if p]
    if not parts:
        click.echo("Error: --root cannot be empty", err=True)
        sys.exit(1)
    parent_id: str | None = None
    for part in parts:
        parent_id = find_folder(drive, part, parent_id=parent_id)
        if parent_id is None:
            click.echo(f"Error: folder segment '{part}' not found in Drive", err=True)
            sys.exit(1)
    return parent_id  # type: ignore[return-value]


def _walk(drive, root_id: str) -> list[tuple[str, str, str]]:
    """BFS walk. Returns list of (parent_rel_path, name, mime) for every item under root.

    parent_rel_path is empty string for items directly under the root.
    The root itself is not emitted (it's the queried subtree).
    """
    rows: list[tuple[str, str, str]] = []
    visited: set[str] = set()
    queue: deque[tuple[str, str]] = deque([(root_id, "")])
    while queue:
        folder_id, folder_rel_path = queue.popleft()
        if folder_id in visited:
            continue
        visited.add(folder_id)
        children = _list_children(drive, folder_id)
        _log.info("%s — %d items", folder_rel_path or "<root>", len(children))
        for c in children:
            rows.append((folder_rel_path, c["name"], c["mimeType"]))
            if c["mimeType"] == _FOLDER_MIME:
                child_rel = f"{folder_rel_path}/{c['name']}" if folder_rel_path else c["name"]
                queue.append((c["id"], child_rel))
    return rows


def _write_csv(rows: list[tuple[str, str, str]], out_path: Path) -> None:
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["path", "name", "mime"])
        writer.writerows(rows)
    click.echo(f"Wrote {len(rows)} rows to {out_path}")


@click.command()
@click.option("--root", default="Evernote Migration", show_default=True,
              help="Slash-separated folder path in My Drive.")
@click.option("--output", default="output/drive_tree.csv", show_default=True,
              type=click.Path(path_type=Path), help="Destination CSV file path.")
@click.option("--secrets-folder", default=None, type=click.Path(path_type=Path), help="Folder containing token.json / client_secrets.json")
def main(root: str, output: Path, secrets_folder: Path | None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
    drive = get_services(secrets_folder=secrets_folder)
    root_id = _resolve_root(drive, root)
    rows = _walk(drive, root_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(rows, output)


if __name__ == "__main__":
    main()

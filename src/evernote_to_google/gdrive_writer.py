"""
Google Drive/Docs output mode: write notes to Google Drive.
"""

from __future__ import annotations

from .auth import get_services
from .docs import create_doc
from .drive import ensure_folder_path, file_exists, get_or_create_folder_path, make_description, upload_file
from .parser import Attachment, Note


class GDriveWriter:
    def __init__(self, dest: str) -> None:
        self._drive, self._docs = get_services()
        self._dest = dest
        self._folder_cache: dict[str, tuple[str, str]] = {}

    def dry_run(self) -> str:
        """Create only the root Drive folder. Returns its ID."""
        return get_or_create_folder_path(self._drive, self._dest)

    def _notebook_id(self, note: Note) -> str:
        cache_key = f"{note.stack or ''}/{note.notebook}"
        if cache_key not in self._folder_cache:
            self._folder_cache[cache_key] = ensure_folder_path(
                self._drive, self._dest, note.notebook, stack=note.stack
            )
        _, notebook_id = self._folder_cache[cache_key]
        return notebook_id

    def note_exists(self, note: Note, safe_title: str) -> bool:
        return bool(file_exists(self._drive, safe_title, self._notebook_id(note)))

    def write_doc(self, title: str, plain_text: str, attachments: list[Attachment], note: Note) -> str:
        return create_doc(
            self._drive, self._docs,
            title=title,
            plain_text=plain_text,
            note=note,
            attachments=attachments,
            parent_id=self._notebook_id(note),
            description=make_description(note.created, note.source_url),
            modified_time=note.updated or note.created,
        )

    def write_raw_file(self, name: str, data: bytes, mime_type: str, note: Note) -> str:
        return upload_file(
            self._drive,
            name=name,
            data=data,
            mime_type=mime_type,
            parent_id=self._notebook_id(note),
            description=make_description(note.created, note.source_url),
            modified_time=note.updated or note.created,
        )

"""
Google Drive/Docs output mode: write notes to Google Drive.

Image embedding uses a 3-phase flow per note:
  Phase 1 — Upload all attachments to the notebook folder.
             Images are uploaded as temp files (temp_<title>_<n>.<ext>) and
             given a public permission so the Drive HTML importer can fetch and
             embed them inline.  Non-image siblings are uploaded as permanent
             files (<title>_<n>.<ext>).
  Phase 2 — Build HTML from the note's ENML (replacing <en-media> tags with
             <img> or <a> elements), then import it as a Google Doc via Drive's
             built-in HTML conversion.  Drive handles all formatting (headings,
             bold, italic, links, RTL) natively.
  Phase 3 — Delete the temp image files (they were only needed for embedding).
             If deletion fails the files remain as identifiable orphans
             (temp_<title>_<n>.<ext>).
"""

from __future__ import annotations

import logging
from datetime import datetime

from .auth import get_services
from .display import rtl_display
from .classifier import ensure_extension, is_note_file, note_name_matches, safe_drive_name
from .gdoc import create_doc, update_doc
from .interlinks import DeferredInterlinkNote, rewrite_evernote_links
from .drive_folders import ensure_folder_path, find_folder_path, get_or_create_folder_path
from .drive_files import (
    _list_folder_files_pairs,
    batch_delete_files,
    list_folder_files,
    make_description,
    upload_file,
)
from .models import GDriveModifiedSource
from .parser import Note
from .gdoc_html import enml_to_gdoc_html
from .drive_attachments import upload_attachments, publish_temp_images, delete_temp_images

_log = logging.getLogger(__name__)


class GDriveWriter:
    def __init__(self, dest: str, include_tags: bool = True, secrets_folder=None,
                 modified_source: GDriveModifiedSource = GDriveModifiedSource.CREATED) -> None:
        self._drive = get_services(secrets_folder=secrets_folder)
        self._dest = dest
        self._include_tags = include_tags
        self._modified_source = modified_source
        self._folder_cache: dict[str, tuple[str, str]] = {}
        self._file_cache: dict[str, set[str]] = {}  # notebook_id -> set of file names

    def modified_time_for(self, note: Note) -> datetime | None:
        """Return the timestamp to use as Drive modifiedTime, per the --gdrive-modified policy."""
        if self._modified_source == GDriveModifiedSource.UPDATED:
            return note.updated or note.created
        return note.created or note.updated

    def _note_description(self, note: Note) -> str:
        return make_description(
            note.created, note.updated, note.source_url,
            tags=note.tags if self._include_tags else None,
        )

    def _notebook_folder_id(self, note: Note) -> str:
        cache_key = f"{note.stack or ''}/{note.notebook}"
        if cache_key not in self._folder_cache:
            self._folder_cache[cache_key] = ensure_folder_path(
                self._drive, self._dest, note.notebook, stack=note.stack
            )
            _, notebook_id = self._folder_cache[cache_key]
            self._file_cache[notebook_id] = list_folder_files(self._drive, notebook_id)
        _, notebook_id = self._folder_cache[cache_key]
        return notebook_id

    def _probe_notebook_files(self, note: Note) -> set[str] | None:
        """Return the set of filenames in the note's notebook folder without creating it.
        Returns None if the folder doesn't exist on Drive."""
        cache_key = f"{note.stack or ''}/{note.notebook}"
        if cache_key in self._folder_cache:
            _, notebook_id = self._folder_cache[cache_key]
            return self._file_cache.get(notebook_id, set())
        notebook_id = find_folder_path(self._drive, self._dest, note.notebook, stack=note.stack)
        if notebook_id is None:
            return None
        self._folder_cache[cache_key] = (None, notebook_id)
        self._file_cache[notebook_id] = list_folder_files(self._drive, notebook_id)
        return self._file_cache[notebook_id]

    def note_exists(self, note: Note, safe_title_override: str | None = None) -> bool:
        files = self._probe_notebook_files(note)
        if files is None:
            return False
        safe_title = safe_title_override or safe_drive_name(note.title)
        return note_name_matches(safe_title, files)

    def cleanup_note_files(self, safe_title: str, note: Note) -> None:
        """Delete any Drive files matching safe_title* in the note folder (partial write cleanup)."""
        _log.warning("starting cleanup for note %r", rtl_display(safe_title))
        notebook_id = find_folder_path(self._drive, self._dest, note.notebook, stack=note.stack)
        if notebook_id is None:
            return
        files = _list_folder_files_pairs(self._drive, notebook_id)
        ids_to_delete = [fid for name, fid in files if is_note_file(safe_title, name)]
        if ids_to_delete:
            _log.debug("cleanup: deleting %d partial file(s) for %r", len(ids_to_delete), safe_title)
            batch_delete_files(self._drive, ids_to_delete)

    def _finalize_temp_images(
        self,
        image_file_ids: list[str],
        hash_to_image_url: dict[str, str],
        hash_to_attachment_link: dict[str, tuple[str, str]],
        defer: bool,
    ) -> None:
        """Delete temp image files, or stash state for a deferred second pass."""
        if defer:
            self._deferred_img_url = hash_to_image_url
            self._deferred_link = hash_to_attachment_link
            self._deferred_image_ids = image_file_ids
        else:
            delete_temp_images(self._drive, image_file_ids)

    def write_doc(self, title: str, attachments: list, note: Note, defer_image_cleanup: bool = False, **_kwargs) -> str:
        _log.debug("going to write note %r as gdoc (%d attachments)", rtl_display(title), len(attachments))
        parent_id = self._notebook_folder_id(note)
        description = self._note_description(note)
        modified_time = self.modified_time_for(note)

        image_file_ids, hash_to_image_url, hash_to_attachment_link = upload_attachments(
            self._drive, attachments, note, parent_id, description, modified_time)
        publish_temp_images(self._drive, image_file_ids)

        html = enml_to_gdoc_html(note.enml, hash_to_image_url, hash_to_attachment_link, note.source_url, title=note.title)
        doc_id = create_doc(
            self._drive,
            title=title,
            html=html,
            parent_id=parent_id,
            description=description,
            modified_time=modified_time,
        )

        self._finalize_temp_images(image_file_ids, hash_to_image_url, hash_to_attachment_link, defer_image_cleanup)
        return doc_id

    def pop_deferred_state(self) -> tuple[dict, dict, list] | None:
        """Return and clear the image state saved by write_doc(defer_image_cleanup=True)."""
        if not hasattr(self, "_deferred_img_url"):
            return None
        state = (self._deferred_img_url, self._deferred_link, self._deferred_image_ids)
        del self._deferred_img_url, self._deferred_link, self._deferred_image_ids
        return state

    def rewrite_deferred_interlinks(self, deferred: DeferredInterlinkNote, title_to_drive_file: dict[str, tuple[str, bool]],
                           duplicate_titles: set[str] | None = None) -> tuple[int, int]:
        """Rewrite inter-note links in a previously-migrated doc and update it in place."""
        rewritten_enml, resolved, unresolved = rewrite_evernote_links(
            deferred.enml, title_to_drive_file, note_title=deferred.title, duplicate_titles=duplicate_titles,
        )
        html = enml_to_gdoc_html(
            rewritten_enml,
            deferred.hash_to_image_url,
            deferred.hash_to_attachment_link,
            deferred.source_url,
            title=deferred.title,
        )
        update_doc(self._drive, deferred.doc_id, html, deferred.modified_time)
        delete_temp_images(self._drive, deferred.image_file_ids)
        return resolved, unresolved

    def write_html_doc(self, title: str, html: str, note: Note) -> str:
        """Create a Google Doc from pre-rendered HTML (e.g. Readability output), bypassing ENML."""
        _log.debug("going to write web clip %r as gdoc (html)", rtl_display(title))
        parent_id = self._notebook_folder_id(note)
        desc = self._note_description(note)
        return create_doc(
            self._drive,
            title=title,
            html=html.encode("utf-8"),
            parent_id=parent_id,
            description=desc,
            modified_time=self.modified_time_for(note),
        )

    def write_raw_file(self, name: str, data: bytes, mime_type: str, note: Note) -> str:
        name = ensure_extension(name, mime_type)
        _log.debug("going to write note %r as raw file [%s]", rtl_display(name), mime_type)
        return upload_file(
            self._drive,
            name=name,
            data=data,
            mime_type=mime_type,
            parent_id=self._notebook_folder_id(note),
            description=self._note_description(note),
            modified_time=self.modified_time_for(note),
        )

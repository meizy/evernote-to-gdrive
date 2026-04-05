"""
Google Drive/Docs output mode: write notes to Google Drive.

Image embedding uses a 3-phase flow per note:
  Phase 1 — Upload all attachments to the notebook folder.
             Images are uploaded as temp files (<title>_img_<n>.<ext>) and
             given a public permission so the Drive HTML importer can fetch and
             embed them inline.  Non-image siblings are uploaded as permanent
             files (<title>_<n>.<ext>).
  Phase 2 — Build HTML from the note's ENML (replacing <en-media> tags with
             <img> or <a> elements), then import it as a Google Doc via Drive's
             built-in HTML conversion.  Drive handles all formatting (headings,
             bold, italic, links, RTL) natively.
  Phase 3 — Delete the temp image files (they were only needed for embedding).
             If deletion fails the files remain as identifiable orphans
             (<title>_img_<n>.<ext>).
"""

from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

from .auth import get_services
from .display import rtl_display
from ._enml import sanitize_enml, parse_media_tag
from ._image import apply_exif_orientation
from .classifier import (
    _EMBEDDABLE_IMAGE_MIME,
    attachment_sibling_filename,
    image_temp_filename,
)
from .docs import create_doc, update_doc
from .interlinks import DeferredNote, rewrite_evernote_links
from .drive import (
    _retry,
    _write_retry,
    batch_delete_files,
    batch_set_permissions,
    drive_url,
    ensure_folder_path,
    get_or_create_folder_path,
    list_folder_files,
    make_description,
    upload_file,
)
from .parser import Attachment, Note


def _delete_image_files(drive, image_file_ids: list[str]) -> None:
    if not image_file_ids:
        return
    if len(image_file_ids) == 1:
        fid = image_file_ids[0]
        _log.debug("deleting temp image file %s", fid)
        _write_retry(drive.files().delete(fileId=fid).execute, op=f"delete temp image '{fid}'")
    else:
        _log.debug("batch-deleting %d temp image files", len(image_file_ids))
        batch_delete_files(drive, image_file_ids)


def _enml_to_html(
    enml: str,
    hash_to_img_url: dict[str, str],
    hash_to_link: dict[str, tuple[str, str]],
    source_url: str | None = None,
    title: str = "",
) -> bytes:
    """
    Convert ENML to HTML suitable for Drive's import converter.

    - Strips <?xml> declaration and <!DOCTYPE>
    - Strips <en-note> wrapper (re-wrapped in <div> below)
    - Replaces <en-media> with <p><img src="..."/></p> for images, or
      <p><a href="...">filename</a></p> for other attachments
    - Prepends a source URL link when present
    """
    _MAX_WIDTH = 500  # px — fits a standard Google Doc page with margins

    def _replace(m: re.Match) -> str:
        h, _ = parse_media_tag(m.group(0))
        if not h:
            return ""
        if h in hash_to_img_url:
            return (f'<p style="text-align:center">'
                    f'<img src="{hash_to_img_url[h]}" width="{_MAX_WIDTH}px"/></p>')
        if h in hash_to_link:
            filename, url = hash_to_link[h]
            return f'<p><a href="{url}">[{filename}]</a></p>'
        return ""

    html = sanitize_enml(enml, _replace, title=title)
    html = f"<div>{html}</div>"
    if source_url:
        header = f'<p>Source: <a href="{source_url}">{source_url}</a></p>'
        html = html.replace("<div>", f"<div>{header}", 1)
    return html.encode("utf-8")


class GDriveWriter:
    def __init__(self, dest: str, include_tags: bool = True) -> None:
        self._drive = get_services()
        self._dest = dest
        self._include_tags = include_tags
        self._folder_cache: dict[str, tuple[str, str]] = {}
        self._file_cache: dict[str, set[str]] = {}  # notebook_id -> set of file names

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
            self._file_cache[notebook_id] = list_folder_files(self._drive, notebook_id)
        _, notebook_id = self._folder_cache[cache_key]
        return notebook_id

    def note_exists(self, note: Note, safe_title: str, exact: bool = False) -> bool:
        notebook_id = self._notebook_id(note)
        return safe_title in self._file_cache.get(notebook_id, set())

    def write_doc(self, title: str, attachments: list[Attachment], note: Note, defer_image_cleanup: bool = False, **_kwargs) -> str:
        _log.debug("going to write note %r as gdoc (%d attachments)", rtl_display(title), len(attachments))
        parent_id = self._notebook_id(note)
        desc = make_description(note.created, note.source_url, tags=note.tags if self._include_tags else None)
        mtime = note.updated or note.created

        # Phase 1: upload all attachments and build URL maps.
        # Images → temp files (<title>_img_<n>.<ext>), deleted after embedding.
        # Non-images → permanent sibling files (<title>_<n>.<ext>).
        img_idx = 0
        sib_idx = 0
        image_file_ids: list[str] = []
        hash_to_img_url: dict[str, str] = {}
        hash_to_link: dict[str, tuple[str, str]] = {}

        _MAX_IMAGES = 100
        skipped_images = 0
        for att in attachments:
            is_image = att.mime in _EMBEDDABLE_IMAGE_MIME
            if is_image and len(image_file_ids) >= _MAX_IMAGES:
                skipped_images += 1
                continue

            if is_image:
                img_idx += 1
                filename = image_temp_filename(note.title, img_idx, att)
                upload_data = apply_exif_orientation(att.data, att.mime)
            else:
                sib_idx += 1
                filename = attachment_sibling_filename(note.title, sib_idx, att)
                upload_data = att.data

            file_id = upload_file(
                self._drive,
                name=filename,
                data=upload_data,
                mime_type=att.mime,
                parent_id=parent_id,
                description=desc,
                modified_time=mtime,
            )

            if is_image:
                hash_to_img_url[att.hash] = f"https://drive.google.com/uc?export=download&id={file_id}"
                image_file_ids.append(file_id)
            else:
                hash_to_link[att.hash] = (filename, drive_url(file_id))

        if skipped_images:
            _log.warning("note %r: skipped %d image(s) exceeding the 100-image limit", rtl_display(title), skipped_images)

        # Phase 1b: set public permissions on temp image files
        if len(image_file_ids) == 1:
            fid = image_file_ids[0]
            _log.debug("making image file %s public", fid)
            _write_retry(
                self._drive.permissions().create(
                    fileId=fid,
                    body={"role": "reader", "type": "anyone"},
                ).execute,
                op=f"set permission on '{fid}'",
            )
        elif image_file_ids:
            _log.debug("batch-setting permissions on %d images", len(image_file_ids))
            batch_set_permissions(self._drive, image_file_ids)

        # Phase 2: build HTML and import as Google Doc
        html = _enml_to_html(note.enml, hash_to_img_url, hash_to_link, note.source_url, title=note.title)
        doc_id = create_doc(
            self._drive,
            title=title,
            html=html,
            parent_id=parent_id,
            description=desc,
            modified_time=mtime,
        )

        # Phase 3: delete temp image files (images are always embedded, never kept as siblings).
        # Deferred when the note has inter-note links that need a second pass.
        if defer_image_cleanup:
            self._deferred_img_url = hash_to_img_url
            self._deferred_link = hash_to_link
            self._deferred_image_ids = image_file_ids
        else:
            _delete_image_files(self._drive, image_file_ids)

        return doc_id

    def pop_deferred_state(self) -> tuple[dict, dict, list] | None:
        """Return and clear the image state saved by write_doc(defer_image_cleanup=True)."""
        if not hasattr(self, "_deferred_img_url"):
            return None
        state = (self._deferred_img_url, self._deferred_link, self._deferred_image_ids)
        del self._deferred_img_url, self._deferred_link, self._deferred_image_ids
        return state

    def rewrite_interlinks(self, deferred: DeferredNote, title_to_doc_id: dict[str, str],
                           duplicate_titles: set[str] | None = None) -> tuple[int, int]:
        """Rewrite inter-note links in a previously-migrated doc and update it in place."""
        rewritten_enml, resolved, unresolved = rewrite_evernote_links(
            deferred.enml, title_to_doc_id, note_title=deferred.title, duplicate_titles=duplicate_titles,
        )
        html = _enml_to_html(
            rewritten_enml,
            deferred.hash_to_img_url,
            deferred.hash_to_link,
            deferred.source_url,
            title=deferred.title,
        )
        update_doc(self._drive, deferred.doc_id, html, deferred.modified_time)
        _delete_image_files(self._drive, deferred.image_file_ids)
        return resolved, unresolved

    def write_raw_file(self, name: str, data: bytes, mime_type: str, note: Note) -> str:
        _log.debug("going to write note %r as raw file [%s]", rtl_display(name), mime_type)
        return upload_file(
            self._drive,
            name=name,
            data=data,
            mime_type=mime_type,
            parent_id=self._notebook_id(note),
            description=make_description(note.created, note.source_url, tags=note.tags if self._include_tags else None),
            modified_time=note.updated or note.created,
        )

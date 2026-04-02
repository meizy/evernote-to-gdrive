"""
Google Drive/Docs output mode: write notes to Google Drive.

Image embedding uses a 2-phase flow per note:
  Phase 1 — Upload all attachments (images + PDFs/other) to the notebook folder.
             Images also get a public permission so the Drive HTML importer can
             fetch them and embed them inline.
  Phase 2 — Build HTML from the note's ENML (replacing <en-media> tags with
             <img> or <a> elements), then import it as a Google Doc via Drive's
             built-in HTML conversion.  Drive handles all formatting (headings,
             bold, italic, links, RTL) natively.
  Phase 3 — If policy is 'doc': delete the temp image files (they were only
             needed for embedding). Non-image files always stay.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING

_log = logging.getLogger(__name__)

from .auth import get_services
from .classifier import (
    _EMBEDDABLE_IMAGE_MIME,
    attachment_label,
    attachment_sibling_filename,
)
from .docs import create_doc
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

if TYPE_CHECKING:
    from .migrate import AttachmentPolicy


def _enml_to_html(
    enml: str,
    hash_to_img_url: dict[str, str],
    hash_to_link: dict[str, tuple[str, str]],
    source_url: str | None = None,
) -> bytes:
    """
    Convert ENML to HTML suitable for Drive's import converter.

    - Strips <?xml> declaration and <!DOCTYPE>
    - Replaces <en-note> wrapper with <div>
    - Replaces <en-media> with <p><img src="..."/></p> for images, or
      <p><a href="...">filename</a></p> for other attachments
    - Prepends a source URL link when present
    """
    html = enml.strip()
    html = re.sub(r"<\?xml[^?]*\?>\s*", "", html)
    html = re.sub(r"<!DOCTYPE[^>]*>\s*", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<en-note\b[^>]*>", "<div>", html, count=1)
    html = html.replace("</en-note>", "</div>")

    def _replace_media(m: re.Match) -> str:
        tag = m.group(0)
        h = re.search(r'\bhash="([0-9a-fA-F]+)"', tag)
        if not h:
            return ""
        hash_val = h.group(1)
        if hash_val in hash_to_img_url:
            return f'<p style="text-align:center"><img src="{hash_to_img_url[hash_val]}"/></p>'
        if hash_val in hash_to_link:
            filename, url = hash_to_link[hash_val]
            return f'<p><a href="{url}">[{filename}]</a></p>'
        return ""

    html = re.sub(r"<en-media\b[^>]*/?>", _replace_media, html)

    if source_url:
        header = f'<p>Source: <a href="{source_url}">{source_url}</a></p>'
        html = html.replace("<div>", f"<div>{header}", 1)

    return html.encode("utf-8")


class GDriveWriter:
    def __init__(self, dest: str, policy: "AttachmentPolicy") -> None:
        self._drive = get_services()
        self._dest = dest
        self._policy = policy
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

    def note_exists(self, note: Note, safe_title: str) -> bool:
        notebook_id = self._notebook_id(note)
        return safe_title in self._file_cache.get(notebook_id, set())

    def write_doc(self, title: str, plain_text: str, attachments: list[Attachment], note: Note, policy: "AttachmentPolicy | None" = None) -> str:
        policy = policy or self._policy
        _log.debug("going to write note %r as gdoc (%d attachments)", title, len(attachments))
        parent_id = self._notebook_id(note)
        desc = make_description(note.created, note.source_url)
        mtime = note.updated or note.created

        # Phase 1: upload all attachments and build URL maps
        counters: dict[str, int] = defaultdict(int)
        image_file_ids: list[str] = []
        hash_to_img_url: dict[str, str] = {}
        hash_to_link: dict[str, tuple[str, str]] = {}

        for att in attachments:
            label = attachment_label(att.mime)
            counters[label] += 1
            filename = attachment_sibling_filename(note.title, label, counters[label], att)

            file_id = upload_file(
                self._drive,
                name=filename,
                data=att.data,
                mime_type=att.mime,
                parent_id=parent_id,
                description=desc,
                modified_time=mtime,
            )

            if att.mime in _EMBEDDABLE_IMAGE_MIME:
                hash_to_img_url[att.hash] = f"https://drive.google.com/uc?export=download&id={file_id}"
                image_file_ids.append(file_id)
            else:
                hash_to_link[att.hash] = (filename, drive_url(file_id))

        # Phase 1b: set public permissions on images
        if len(image_file_ids) > 2:
            _log.debug("batch-setting permissions on %d images", len(image_file_ids))
            batch_set_permissions(self._drive, image_file_ids)
        else:
            for fid in image_file_ids:
                _log.debug("going to make image file %s public (permissions.create)", fid)
                _write_retry(
                    self._drive.permissions().create(
                        fileId=fid,
                        body={"role": "reader", "type": "anyone"},
                    ).execute
                )
                _log.debug("image file %s made public", fid)

        # Phase 2: build HTML and import as Google Doc
        html = _enml_to_html(note.enml, hash_to_img_url, hash_to_link, note.source_url)
        doc_id = create_doc(
            self._drive,
            title=title,
            html=html,
            parent_id=parent_id,
            description=desc,
            modified_time=mtime,
        )

        # Phase 3: delete temp image files if policy is 'doc'
        if policy == "doc" and image_file_ids:
            if len(image_file_ids) > 2:
                _log.debug("batch-deleting %d temp image files", len(image_file_ids))
                batch_delete_files(self._drive, image_file_ids)
            else:
                for fid in image_file_ids:
                    _log.debug("going to delete temp image file %s (files.delete)", fid)
                    _write_retry(self._drive.files().delete(fileId=fid).execute)
                    _log.debug("temp image file %s deleted", fid)

        return doc_id

    def write_raw_file(self, name: str, data: bytes, mime_type: str, note: Note) -> str:
        _log.debug("going to write note %r as raw file [%s]", name, mime_type)
        return upload_file(
            self._drive,
            name=name,
            data=data,
            mime_type=mime_type,
            parent_id=self._notebook_id(note),
            description=make_description(note.created, note.source_url),
            modified_time=note.updated or note.created,
        )

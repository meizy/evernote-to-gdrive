"""
Google Drive/Docs output mode: write notes to Google Drive.

Image embedding uses a 3-phase flow per note:
  Phase 1 — Upload all attachments (images + PDFs/other) to the notebook folder.
             Images also get a public permission so insertInlineImage can fetch them.
  Phase 2 — Create the Google Doc with one batchUpdate: all text + inline images
             (using public Drive URLs) + hyperlinks to non-image files.
  Phase 3 — If policy is 'doc': delete the temp image files (they were only needed
             for embedding). Non-image files always stay. If policy is 'both': keep all.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from .auth import get_services
from .classifier import (
    _EMBEDDABLE_IMAGE_MIME,
    attachment_label,
    attachment_sibling_filename,
    enml_to_text,
)
from .docs import DocImage, DocLink, _image_size_pt, create_doc
from .drive import (
    _retry,
    drive_url,
    ensure_folder_path,
    file_exists,
    get_or_create_folder_path,
    make_description,
    upload_file,
)
from .parser import Attachment, Note

if TYPE_CHECKING:
    from .migrate import AttachmentPolicy


class GDriveWriter:
    def __init__(self, dest: str, policy: "AttachmentPolicy") -> None:
        self._drive, self._docs = get_services()
        self._dest = dest
        self._policy = policy
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

    def write_doc(self, title: str, plain_text: str, attachments: list[Attachment], note: Note, policy: "AttachmentPolicy | None" = None, segments: list | None = None) -> str:
        policy = policy or self._policy
        parent_id = self._notebook_id(note)
        desc = make_description(note.created, note.source_url)
        mtime = note.updated or note.created

        # Phase 1: upload all attachments and build resolved references
        counters: dict[str, int] = defaultdict(int)
        image_file_ids: list[str] = []  # tracked for optional cleanup in Phase 3
        resolved: list[DocImage | DocLink] = []
        hash_to_resolved: dict[str, DocImage | DocLink] = {}

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
                # Grant public read access so the Docs API can fetch the image
                _retry(
                    self._drive.permissions().create(
                        fileId=file_id,
                        body={"role": "reader", "type": "anyone"},
                    ).execute
                )
                url = f"https://drive.google.com/uc?export=download&id={file_id}"
                w_pt, h_pt = _image_size_pt(att.data)
                img = DocImage(url=url, width_pt=w_pt, height_pt=h_pt)
                resolved.append(img)
                hash_to_resolved[att.hash] = img
                image_file_ids.append(file_id)
            else:
                link = DocLink(label=f"[{filename}]", url=drive_url(file_id))
                resolved.append(link)
                hash_to_resolved[att.hash] = link

        # When segments are available, build an ordered list of text + inline attachments
        # so images appear at their correct positions instead of all at the end.
        body_segments: list | None = None
        if segments is not None and hash_to_resolved:
            body_segments = []
            for seg in segments:
                if isinstance(seg, str):
                    text = enml_to_text(seg)
                    if text:
                        body_segments.append(text)
                else:  # Attachment
                    r = hash_to_resolved.get(seg.hash)
                    if r:
                        body_segments.append(r)

        # Phase 2: create the doc with a single batchUpdate
        doc_id = create_doc(
            self._drive,
            self._docs,
            title=title,
            plain_text=plain_text,
            note=note,
            resolved_attachments=resolved,
            body_segments=body_segments,
            parent_id=parent_id,
            description=desc,
            modified_time=mtime,
        )

        # Phase 3: delete temp image files if policy is 'doc'
        if policy == "doc":
            for fid in image_file_ids:
                _retry(self._drive.files().delete(fileId=fid).execute)

        return doc_id

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

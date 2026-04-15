"""
Attachment upload/publish/delete lifecycle for Google Drive notes.
"""

from __future__ import annotations

import logging

from .drive_retry import _write_retry
from .drive_files import batch_delete_files, batch_set_permissions, drive_image_url, drive_url, upload_file
from .classifier import _EMBEDDABLE_IMAGE_MIME, attachment_sibling_filename, image_temp_filename
from ._image import apply_exif_orientation
from .display import rtl_display
from .parser import Attachment, Note

_log = logging.getLogger(__name__)


def upload_attachments(
    drive,
    attachments: list[Attachment],
    note: Note,
    parent_id: str,
    description: str,
    modified_time: str,
) -> tuple[list[str], dict[str, str], dict[str, tuple[str, str]]]:
    """Upload all attachments; return (image_file_ids, hash_to_image_url, hash_to_attachment_link)."""
    _MAX_IMAGES = 100
    image_index = 0
    sibling_index = 0
    image_file_ids: list[str] = []
    hash_to_image_url: dict[str, str] = {}
    hash_to_attachment_link: dict[str, tuple[str, str]] = {}
    skipped_images = 0

    for attachment in attachments:
        is_image = attachment.mime in _EMBEDDABLE_IMAGE_MIME
        if is_image and len(image_file_ids) >= _MAX_IMAGES:
            skipped_images += 1
            continue

        if is_image:
            image_index += 1
            filename = image_temp_filename(note.title, image_index, attachment)
            upload_data = apply_exif_orientation(attachment.data, attachment.mime)
        else:
            sibling_index += 1
            filename = attachment_sibling_filename(note.title, sibling_index, attachment)
            upload_data = attachment.data

        file_id = upload_file(
            drive,
            name=filename,
            data=upload_data,
            mime_type=attachment.mime,
            parent_id=parent_id,
            description=description,
            modified_time=modified_time,
        )

        if is_image:
            hash_to_image_url[attachment.hash] = drive_image_url(file_id)
            image_file_ids.append(file_id)
        else:
            hash_to_attachment_link[attachment.hash] = (filename, drive_url(file_id))

    if skipped_images:
        _log.warning("note %r: skipped %d image(s) exceeding the 100-image limit", rtl_display(note.title), skipped_images)

    return image_file_ids, hash_to_image_url, hash_to_attachment_link


def publish_temp_images(drive, image_file_ids: list[str]) -> None:
    """Grant public read access to temp image files so Drive's importer can fetch them."""
    if len(image_file_ids) == 1:
        file_id = image_file_ids[0]
        _log.debug("making image file %s public", file_id)
        _write_retry(
            drive.permissions().create(
                fileId=file_id,
                body={"role": "reader", "type": "anyone"},
            ).execute,
            op=f"set permission on '{file_id}'",
        )
    elif image_file_ids:
        _log.debug("batch-setting permissions on %d images", len(image_file_ids))
        batch_set_permissions(drive, image_file_ids)


def delete_temp_images(drive, image_file_ids: list[str]) -> None:
    """Delete temp image files from Drive."""
    if not image_file_ids:
        return
    if len(image_file_ids) == 1:
        file_id = image_file_ids[0]
        _log.debug("deleting temp image file %s", file_id)
        _write_retry(drive.files().delete(fileId=file_id).execute, op=f"delete temp image '{file_id}'")
    else:
        _log.debug("batch-deleting %d temp image files", len(image_file_ids))
        batch_delete_files(drive, image_file_ids)

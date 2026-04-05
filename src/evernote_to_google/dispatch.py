"""
Per-note classification and write dispatch.
"""

from __future__ import annotations

import logging
from dataclasses import replace as dc_replace

from googleapiclient.errors import HttpError

from .classifier import NoteKind, attachment_sibling_filename, classify, _safe_name, _EMBEDDABLE_IMAGE_MIME
from .display import rtl_display
from .drive import get_bytes_uploaded
from .interlinks import DeferredNote, has_interlinks, rewrite_evernote_links
from .models import AttachmentPolicy, MigrationOptions, MigrationRecord, MigrationStatus, OutputMode
from .parser import Note


_log = logging.getLogger(__name__)


def _has_doc_siblings(attachments: list) -> bool:
    """Return True if the doc will have sibling files (determines whether _0 suffix is needed).
    Images are always embedded, so only non-embeddable attachments become siblings."""
    return any(a.mime not in _EMBEDDABLE_IMAGE_MIME for a in attachments)


def _write_note(note: Note, classified, safe_title: str, eff_title: str, policy: AttachmentPolicy, options: MigrationOptions, writer, defer_cleanup: bool = False) -> list[str]:
    kind = classified.kind
    attachments = classified.attachments
    extra = {"defer_image_cleanup": True} if defer_cleanup else {}

    if kind == NoteKind.TEXT_ONLY:
        return [writer.write_doc(safe_title, [], note, **extra)]

    if kind == NoteKind.ATTACHMENT_ONLY_SINGLE:
        att = attachments[0]
        return [writer.write_raw_file(safe_title, att.data, att.mime, note)]

    has_images = any(a.mime in _EMBEDDABLE_IMAGE_MIME for a in attachments)

    if kind == NoteKind.ATTACHMENT_ONLY_MULTI:
        # FILES policy only applies when there are no images (all non-embeddable).
        # If any images are present they must be embedded, so always use doc route.
        if not has_images and policy == AttachmentPolicy.FILES:
            output = []
            for idx, att in enumerate(attachments, 1):
                filename = attachment_sibling_filename(eff_title, idx, att)
                output.append(writer.write_raw_file(filename, att.data, att.mime, note))
            return output
        has_siblings = _has_doc_siblings(attachments)
        doc_title = f"{safe_title}_0" if has_siblings else safe_title
        return [writer.write_doc(doc_title, attachments, note, **extra)]

    if kind == NoteKind.TEXT_WITH_ATTACHMENTS:
        has_siblings = _has_doc_siblings(attachments)
        doc_title = f"{safe_title}_0" if has_siblings else safe_title
        return [writer.write_doc(doc_title, attachments, note, **extra)]

    raise ValueError(f"Unhandled note kind: {kind}")


def migrate_note(
    note: Note,
    options: MigrationOptions,
    writer,
    seen: dict[tuple[str, str], int],
    deferred_notes: list[DeferredNote] | None = None,
) -> MigrationRecord:
    classified = classify(note)
    kind_label = classified.kind.name.lower()
    safe_title = _safe_name(note.title)
    eff_title = note.title  # may get a ` (N)` suffix for local-mode duplicates

    policy = options.attachments

    try:
        key = (note.notebook, safe_title)
        if key in seen:
            seen[key] += 1
            if options.output_mode == OutputMode.LOCAL:
                eff_title = f"{note.title} ({seen[key]})"
                safe_title = _safe_name(eff_title)
                if writer.note_exists(note, safe_title):
                    return MigrationRecord(
                        notebook=note.notebook, title=note.title, kind=kind_label,
                        status=MigrationStatus.SKIPPED, output=[],
                    )
            # gdrive: keep original name — Drive allows same-name files
        else:
            seen[key] = 1
            # For attachment-only-multi with FILES policy (all non-images), files are
            # stored under sibling filenames, not safe_title — check the first one instead.
            if (classified.kind == NoteKind.ATTACHMENT_ONLY_MULTI
                    and policy == AttachmentPolicy.FILES
                    and classified.attachments
                    and not any(a.mime in _EMBEDDABLE_IMAGE_MIME for a in classified.attachments)):
                att0 = classified.attachments[0]
                check_name = attachment_sibling_filename(note.title, 1, att0)
            else:
                check_name = safe_title

            is_full_filename = (classified.kind == NoteKind.ATTACHMENT_ONLY_MULTI
                                and policy == AttachmentPolicy.FILES)
            if writer.note_exists(note, check_name, exact=is_full_filename):
                return MigrationRecord(
                    notebook=note.notebook, title=note.title, kind=kind_label,
                    status=MigrationStatus.SKIPPED, output=[],
                )

        gdrive = options.output_mode == OutputMode.GOOGLE
        note_enml = note.enml or ""
        interlinked = gdrive and has_interlinks(note_enml) and not options.skip_note_links

        # In --note mode there is no pass 2: rewrite links to "not resolved" inline.
        if interlinked and options.note:
            note_enml, _, _ = rewrite_evernote_links(note_enml, {}, note_title=note.title)
            note = dc_replace(note, enml=note_enml)
            interlinked = False

        output = _write_note(note, classified, safe_title, eff_title, policy, options, writer,
                             defer_cleanup=interlinked)

        if interlinked and deferred_notes is not None and output:
            state = writer.pop_deferred_state()
            if state:
                img_url, link, image_ids = state
                deferred_notes.append(DeferredNote(
                    title=note.title,
                    doc_id=output[0],
                    enml=note_enml,
                    hash_to_img_url=img_url,
                    hash_to_link=link,
                    source_url=note.source_url,
                    modified_time=note.updated or note.created,
                    image_file_ids=image_ids,
                    include_tags=options.include_tags,
                    tags=list(note.tags),
                ))

        return MigrationRecord(
            notebook=note.notebook, title=note.title, kind=kind_label,
            status=MigrationStatus.SUCCESS, output=output,
        )

    except Exception as exc:
        error_msg = str(exc)
        if isinstance(exc, HttpError) and exc.status_code in (403, 429):
            gb_uploaded = get_bytes_uploaded() / 1024 ** 3
            if gb_uploaded > 100:
                error_msg += (
                    f" | You've uploaded ~{gb_uploaded:.0f} GB this session."
                    " This may be the 750 GB daily upload limit —"
                    " resume tomorrow with the same command (completed notes will be skipped)."
                )
        _log.error("%s: %s (%s)", rtl_display(note.title), error_msg, type(exc).__name__)
        return MigrationRecord(
            notebook=note.notebook, title=note.title, kind=kind_label,
            status=MigrationStatus.ERROR, output=[], error=error_msg,
        )

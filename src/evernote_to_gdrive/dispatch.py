"""
Per-note classification and write dispatch.
"""

from __future__ import annotations

import logging
from dataclasses import replace as dc_replace

from googleapiclient.errors import HttpError

from .classifier import (
    NoteKind,
    _all_non_image,
    attachment_sibling_filename,
    classify,
    safe_drive_name,
    safe_local_name,
    _EMBEDDABLE_IMAGE_MIME,
)
from .display import notebook_path, rtl_display
from .drive_retry import get_bytes_uploaded
from .interlinks import DeferredInterlinkNote, LocalDeferredInterlinkNote, has_interlinks, rewrite_evernote_links
from .models import AttachmentPolicy, MigrationOptions, MigrationRecord, MigrationStatus, OutputMode, WebClipMode, WriterProtocol
from .parser import Note


_log = logging.getLogger(__name__)


def _safe_output_name(name: str, mode: OutputMode) -> str:
    return safe_local_name(name) if mode == OutputMode.LOCAL else safe_drive_name(name)


def _write_web_clip(note: Note, safe_title: str, options: MigrationOptions, writer: WriterProtocol, renderer) -> list[str] | None:
    """Process a web clip through the Readability pipeline. Returns None if Readability can't parse."""
    if options.web_clip == WebClipMode.PDF:
        pdf_bytes = renderer.render_pdf(note)
        if pdf_bytes is None:
            return None
        return [writer.write_raw_file(safe_title, pdf_bytes, "application/pdf", note)]
    else:  # DOC
        html = renderer.render_html(note)
        if html is None:
            return None
        return [writer.write_html_doc(safe_title, html, note)]


def _has_doc_siblings(attachments: list) -> bool:
    """Return True if the doc will have sibling files (determines whether _0 suffix is needed).
    Images are always embedded, so only non-embeddable attachments become siblings."""
    return any(a.mime not in _EMBEDDABLE_IMAGE_MIME for a in attachments)


def _output_name(classified, safe_title: str, policy: AttachmentPolicy) -> str:
    """Compute the base output name (no extension) for the primary file of a note."""
    kind = classified.kind
    if kind in (NoteKind.TEXT_ONLY, NoteKind.ATTACHMENT_ONLY_SINGLE):
        return safe_title
    if kind == NoteKind.ATTACHMENT_ONLY_MULTI and _all_non_image(classified.attachments) and policy == AttachmentPolicy.FILES:
        return safe_title
    return f"{safe_title}_0" if _has_doc_siblings(classified.attachments) else safe_title


def _sibling_count(classified, policy: AttachmentPolicy) -> int:
    """Count non-image attachments written as sibling files alongside a doc."""
    kind = classified.kind
    atts = classified.attachments
    if kind in (NoteKind.TEXT_ONLY, NoteKind.ATTACHMENT_ONLY_SINGLE):
        return 0
    if kind == NoteKind.ATTACHMENT_ONLY_MULTI and _all_non_image(atts) and policy == AttachmentPolicy.FILES:
        return 0  # all raw files, no primary doc
    return sum(1 for a in atts if a.mime not in _EMBEDDABLE_IMAGE_MIME)


def _write_note(note: Note, classified, safe_title: str, eff_title: str, policy: AttachmentPolicy, options: MigrationOptions, writer: WriterProtocol, defer_cleanup: bool = False) -> tuple[list[str], bool]:
    kind = classified.kind
    attachments = classified.attachments
    extra = {"defer_image_cleanup": True} if defer_cleanup else {}

    if kind == NoteKind.TEXT_ONLY:
        return [writer.write_doc(safe_title, [], note, **extra)], True

    if kind == NoteKind.ATTACHMENT_ONLY_SINGLE:
        att = attachments[0]
        return [writer.write_raw_file(safe_title, att.data, att.mime, note)], False

    if (kind == NoteKind.ATTACHMENT_ONLY_MULTI
            and _all_non_image(attachments) and policy == AttachmentPolicy.FILES):
        # FILES policy only applies when there are no images (all non-embeddable).
        # If any images are present they must be embedded, so always use doc route.
        output = []
        for idx, att in enumerate(attachments, 1):
            filename = attachment_sibling_filename(eff_title, idx, att)
            output.append(writer.write_raw_file(filename, att.data, att.mime, note))
        return output, False

    if kind in (NoteKind.ATTACHMENT_ONLY_MULTI, NoteKind.TEXT_WITH_ATTACHMENTS):
        has_siblings = _has_doc_siblings(attachments)
        doc_title = f"{safe_title}_0" if has_siblings else safe_title
        return [writer.write_doc(doc_title, attachments, note, eff_title=safe_title, **extra)], True

    raise ValueError(f"Unhandled note kind: {kind}")


def _resolve_output_name(
    note: Note,
    safe_title: str,
    kind_label: str,
    options: MigrationOptions,
    writer: WriterProtocol,
    seen: dict[tuple[str, str], int],
) -> MigrationRecord | tuple[str, str]:
    """Track duplicate titles and check existence. Returns (safe_title, eff_title) or a SKIPPED record."""
    key = (note.notebook, safe_title)
    eff_title = note.title
    if key in seen:
        seen[key] += 1
        if options.output_mode == OutputMode.LOCAL:
            eff_title = f"{note.title} ({seen[key]})"
            safe_title = _safe_output_name(eff_title, options.output_mode)
            if not options.force and writer.note_exists(note, safe_title_override=safe_title):
                return MigrationRecord(
                    notebook=notebook_path(note.stack, note.notebook), title=note.title, kind=kind_label,
                    status=MigrationStatus.SKIPPED, output=[],
                )
        # gdrive: keep original name — Drive allows same-name files
    else:
        seen[key] = 1
        if not options.force and writer.note_exists(note):
            return MigrationRecord(
                notebook=notebook_path(note.stack, note.notebook), title=note.title, kind=kind_label,
                status=MigrationStatus.SKIPPED, output=[],
            )
    return safe_title, eff_title


def _handle_web_clip(
    note: Note,
    safe_title: str,
    options: MigrationOptions,
    writer: WriterProtocol,
    renderer,
) -> MigrationRecord | None:
    """Attempt Readability rendering. Returns a SUCCESS record or None (fall through to normal rendering)."""
    if note.source_url is None:
        return None
    output = _write_web_clip(note, safe_title, options, writer, renderer)
    if output is not None:
        return MigrationRecord(
            notebook=notebook_path(note.stack, note.notebook), title=note.title, kind="web_clip",
            status=MigrationStatus.SUCCESS, output=output,
            is_doc=options.web_clip != WebClipMode.PDF,
            output_name=safe_title,
        )
    _log.debug("Readability could not parse '%s' — falling back to normal note rendering", rtl_display(note.title))
    return None


def _maybe_defer_interlinks(
    note: Note,
    note_enml: str,
    output: list[str],
    writer: WriterProtocol,
    deferred_notes: list | None,
    mode: OutputMode,
) -> None:
    """Capture deferred state for pass-2 interlink rewriting."""
    if deferred_notes is None or not output:
        return
    state = writer.pop_deferred_state()  # type: ignore[attr-defined]
    if state:
        if mode == OutputMode.GOOGLE:
            img_url, link, image_ids = state
            deferred_notes.append(DeferredInterlinkNote(
                title=note.title,
                doc_id=output[0],
                enml=note_enml,
                hash_to_image_url=img_url,
                hash_to_attachment_link=link,
                source_url=note.source_url,
                modified_time=writer.modified_time_for(note),  # type: ignore[attr-defined]
                image_file_ids=image_ids,
            ))
        else:
            docx_path, sibling_filenames, attachments, note_obj = state
            from pathlib import Path as _Path
            deferred_notes.append(LocalDeferredInterlinkNote(
                title=note.title,
                docx_path=_Path(str(docx_path)),
                note=note_obj,
                attachments=attachments,
                sibling_filenames=sibling_filenames,
            ))


def migrate_note(
    note: Note,
    options: MigrationOptions,
    writer: WriterProtocol,
    seen: dict[tuple[str, str], int],
    deferred_notes: list[DeferredInterlinkNote] | None = None,
    renderer=None,
) -> MigrationRecord:
    classified = classify(note)
    kind_label = classified.kind.name.lower()
    safe_title = _safe_output_name(note.title, options.output_mode)
    policy = options.attachments

    try:
        resolved = _resolve_output_name(note, safe_title, kind_label, options, writer, seen)
        if isinstance(resolved, MigrationRecord):
            return resolved
        safe_title, eff_title = resolved

        clip_result = _handle_web_clip(note, safe_title, options, writer, renderer)
        if clip_result is not None:
            return clip_result

        note_enml = note.enml or ""
        interlinked = has_interlinks(note_enml) and not options.skip_note_links

        # In --note mode there is no pass 2: rewrite links to "not resolved" inline.
        if interlinked and options.note:
            note_enml, _, _ = rewrite_evernote_links(note_enml, {}, note_title=note.title)
            note = dc_replace(note, enml=note_enml)
            interlinked = False

        out_name = _output_name(classified, safe_title, policy)
        emb_images = sum(1 for a in classified.attachments if a.mime in _EMBEDDABLE_IMAGE_MIME)
        sib_files = _sibling_count(classified, policy)

        output, is_doc = _write_note(note, classified, safe_title, eff_title, policy, options, writer,
                                      defer_cleanup=interlinked)

        if interlinked:
            _maybe_defer_interlinks(note, note_enml, output, writer, deferred_notes, options.output_mode)

        return MigrationRecord(
            notebook=notebook_path(note.stack, note.notebook), title=note.title, kind=kind_label,
            status=MigrationStatus.SUCCESS, output=output, is_doc=is_doc,
            output_name=out_name, embedded_images=emb_images, sibling_files=sib_files,
        )

    except (NameError, TypeError):
        # Programming bugs — surface loudly instead of hiding them behind an ERROR record.
        raise
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
        try:
            writer.cleanup_note_files(safe_title, note)
        except Exception as cleanup_exc:
            _log.debug("cleanup failed for %r: %s", safe_title, cleanup_exc)
        return MigrationRecord(
            notebook=notebook_path(note.stack, note.notebook), title=note.title, kind=kind_label,
            status=MigrationStatus.ERROR, output=[], error=error_msg,
        )

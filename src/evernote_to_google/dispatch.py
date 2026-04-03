"""
Per-note classification and write dispatch.
"""

from __future__ import annotations

import sys
from collections import defaultdict

from googleapiclient.errors import HttpError

from .classifier import NoteKind, attachment_label, attachment_sibling_filename, classify, _safe_name, _EMBEDDABLE_IMAGE_MIME
from .display import rtl_display
from .drive import get_bytes_uploaded
from .models import AttachmentPolicy, MigrationOptions, MigrationRecord, MigrationStatus, OutputMode
from .parser import Note


def _eprint(*args, **kwargs):
    print(*args, file=sys.stderr, flush=True, **kwargs)


def _has_doc_siblings(attachments: list, policy: AttachmentPolicy) -> bool:
    """Return True if the doc will have sibling files (determines whether _0 suffix is needed)."""
    if policy in (AttachmentPolicy.BOTH, AttachmentPolicy.FILES):
        return len(attachments) > 0
    # DOC: only non-embeddable attachments become siblings
    return any(a.mime not in _EMBEDDABLE_IMAGE_MIME for a in attachments)


def _write_note(note: Note, classified, safe_title: str, eff_title: str, policy: AttachmentPolicy, options: MigrationOptions, writer) -> list[str]:
    kind = classified.kind
    attachments = classified.attachments

    if kind == NoteKind.TEXT_ONLY:
        return [writer.write_doc(safe_title, [], note)]

    if kind == NoteKind.ATTACHMENT_ONLY_SINGLE:
        att = attachments[0]
        return [writer.write_raw_file(safe_title, att.data, att.mime, note)]

    if kind == NoteKind.ATTACHMENT_ONLY_MULTI:
        if policy == AttachmentPolicy.FILES:
            counters: dict[str, int] = defaultdict(int)
            output = []
            for att in attachments:
                label = attachment_label(att.mime)
                counters[label] += 1
                filename = attachment_sibling_filename(eff_title, label, counters[label], att)
                output.append(writer.write_raw_file(filename, att.data, att.mime, note))
            return output
        has_siblings = _has_doc_siblings(attachments, policy)
        doc_title = f"{safe_title}_0" if has_siblings else safe_title
        return [writer.write_doc(doc_title, attachments, note, policy)]

    if kind == NoteKind.TEXT_WITH_ATTACHMENTS:
        # FILES implies BOTH for text notes: the doc must exist for the text,
        # so all attachments are also written as siblings.
        effective = AttachmentPolicy.BOTH if policy == AttachmentPolicy.FILES else policy
        has_siblings = _has_doc_siblings(attachments, effective)
        doc_title = f"{safe_title}_0" if has_siblings else safe_title
        return [writer.write_doc(doc_title, attachments, note, effective)]

    raise ValueError(f"Unhandled note kind: {kind}")


def migrate_note(note: Note, options: MigrationOptions, writer, seen: dict[tuple[str, str], int]) -> MigrationRecord:
    classified = classify(note)
    kind_label = classified.kind.name.lower()
    safe_title = _safe_name(note.title)
    eff_title = note.title  # may get a ` (N)` suffix for local-mode duplicates

    # Web-clipped notes have a source_url. Force doc policy to avoid
    # producing many junk sibling files from page images.
    policy = AttachmentPolicy.DOC if note.source_url else options.attachments

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
            # For attachment-only-multi with FILES policy, files are stored under
            # sibling filenames, not safe_title — check the first one instead.
            if (classified.kind == NoteKind.ATTACHMENT_ONLY_MULTI
                    and policy == AttachmentPolicy.FILES
                    and classified.attachments):
                att0 = classified.attachments[0]
                check_name = attachment_sibling_filename(note.title, attachment_label(att0.mime), 1, att0)
            else:
                check_name = safe_title

            is_full_filename = (classified.kind == NoteKind.ATTACHMENT_ONLY_MULTI
                                and policy == AttachmentPolicy.FILES)
            if writer.note_exists(note, check_name, exact=is_full_filename):
                return MigrationRecord(
                    notebook=note.notebook, title=note.title, kind=kind_label,
                    status=MigrationStatus.SKIPPED, output=[],
                )

        output = _write_note(note, classified, safe_title, eff_title, policy, options, writer)

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
        _eprint(f"Error: {rtl_display(note.title)!r}: {error_msg} ({type(exc).__name__})")
        return MigrationRecord(
            notebook=note.notebook, title=note.title, kind=kind_label,
            status=MigrationStatus.ERROR, output=[], error=error_msg,
        )

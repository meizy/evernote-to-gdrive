"""
Inter-note link detection and rewriting for GDrive and local output modes.

Evernote notes can link to other notes via evernote:///view/... URLs.
Since ENEX files don't include note GUIDs, we match by anchor text (= target note title).

Two-pass approach:
  Pass 1: migrate all notes, cache deferred state for notes with inter-note links.
  Pass 2: rewrite evernote:/// links using title->target map, re-import or regenerate the doc.
"""

from __future__ import annotations

import html
import logging
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .drive_files import drive_url, gdoc_url

_log = logging.getLogger(__name__)

# Matches a full <a href="evernote:///...">...</a> tag (including nested tags in anchor text).
_RE_INTERLINK = re.compile(
    r'<a\b[^>]*\bhref="evernote:///[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)

# Strips any HTML tags from a string (used to extract plain title from anchor content).
_RE_STRIP_TAGS = re.compile(r'<[^>]+>')


def has_interlinks(enml: str) -> bool:
    """Return True if the ENML contains any evernote:/// inter-note link."""
    return "evernote:///" in enml


def count_interlinks(enml: str) -> int:
    """Return the number of evernote:/// inter-note links in ENML."""
    if "evernote:///" not in enml:
        return 0
    return len(_RE_INTERLINK.findall(enml))


def _anchor_title(inner_html: str) -> str:
    """Extract plain text title from anchor inner HTML (strip tags, decode entities)."""
    return html.unescape(_RE_STRIP_TAGS.sub("", inner_html).strip())


def _rewrite_anchors(
    enml: str,
    resolve: Callable[[str], str | None],
    note_title: str,
    duplicate_titles: set[str] | None,
) -> tuple[str, int, int]:
    """Replace evernote:///view/... links in ENML using a caller-supplied resolver.

    resolve(title) returns the new href string, or None if the title is unresolved.
    Unresolved links become plain text: [link to "Title" not resolved].

    Returns (rewritten_enml, resolved_count, unresolved_count).
    """
    resolved = 0
    unresolved = 0

    def _replace(m: re.Match) -> str:
        nonlocal resolved, unresolved
        inner_html = m.group(1)
        title = _anchor_title(inner_html)
        href = resolve(title)
        if href is not None:
            resolved += 1
            if duplicate_titles and title in duplicate_titles:
                _log.warning(
                    "note %r: inter-note link to %r resolved but title has duplicates — may point to wrong doc",
                    note_title,
                    title,
                )
            return f'<a href="{href}">{inner_html}</a>'
        else:
            unresolved += 1
            _log.warning("note %r: inter-note link to %r not resolved", note_title, title)
            label = title if title else "unknown"
            return f'[link to "{label}" not resolved]'

    rewritten = _RE_INTERLINK.sub(_replace, enml)
    return rewritten, resolved, unresolved


def rewrite_evernote_links(
    enml: str,
    title_to_drive_file: dict[str, tuple[str, bool]],
    note_title: str = "",
    duplicate_titles: set[str] | None = None,
) -> tuple[str, int, int]:
    """Replace evernote:///view/... links in ENML with Google Drive URLs.

    Resolved links become <a href="https://docs.google.com/...">Title</a> for Google Docs,
    or <a href="https://drive.google.com/file/...">Title</a> for raw Drive files.
    Unresolved links become plain text: [link to "Title" not resolved].

    Returns (rewritten_enml, resolved_count, unresolved_count).
    """
    def _resolve(title: str) -> str | None:
        entry = title_to_drive_file.get(title)
        if entry is None:
            return None
        file_id, is_doc = entry
        return gdoc_url(file_id) if is_doc else drive_url(file_id)

    return _rewrite_anchors(enml, _resolve, note_title, duplicate_titles)


def rewrite_evernote_links_local(
    enml: str,
    title_to_path: dict[str, tuple[str | Path, bool]],
    source_folder: Path,
    note_title: str = "",
    duplicate_titles: set[str] | None = None,
) -> tuple[str, int, int]:
    """Replace evernote:///view/... links in ENML with relative paths to local .docx files.

    Resolved links become <a href="../Notebook/Note Title.docx">Title</a>.
    Unresolved links become plain text: [link to "Title" not resolved].

    Returns (rewritten_enml, resolved_count, unresolved_count).
    """
    def _resolve(title: str) -> str | None:
        entry = title_to_path.get(title)
        if entry is None:
            return None
        target_path, _ = entry
        rel = os.path.relpath(str(target_path), str(source_folder))
        posix_rel = rel.replace(os.sep, "/")
        return urllib.parse.quote(posix_rel, safe="/")

    return _rewrite_anchors(enml, _resolve, note_title, duplicate_titles)


@dataclass
class DeferredInterlinkNote:
    """Lightweight cache of state needed to rewrite inter-note links in pass 2 (GDrive mode)."""
    title: str
    doc_id: str
    enml: str
    hash_to_image_url: dict[str, str]
    hash_to_attachment_link: dict[str, tuple[str, str]]
    source_url: str | None
    modified_time: datetime | None
    image_file_ids: list[str] = field(default_factory=list)


@dataclass
class LocalDeferredInterlinkNote:
    """Deferred state for local-mode inter-note link rewriting in pass 2."""
    title: str
    docx_path: Path
    note: "Note"
    attachments: list
    sibling_filenames: list[str]

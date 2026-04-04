"""
Inter-note link detection and rewriting for GDrive mode.

Evernote notes can link to other notes via evernote:///view/... URLs.
Since ENEX files don't include note GUIDs, we match by anchor text (= target note title).

Two-pass approach:
  Pass 1: migrate all notes, cache deferred state for notes with inter-note links.
  Pass 2: rewrite evernote:/// links using title->doc_id map, re-import HTML.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

_log = logging.getLogger(__name__)

# Matches a full <a href="evernote:///...">...</a> tag (including nested tags in anchor text).
_RE_INTERLINK = re.compile(
    r'<a\b[^>]*\bhref="evernote:///[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)

# Strips any HTML tags from a string (used to extract plain title from anchor content).
_RE_STRIP_TAGS = re.compile(r'<[^>]+>')

_GDOC_URL = "https://docs.google.com/document/d/{}/edit"


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


def rewrite_evernote_links(
    enml: str,
    title_to_doc_id: dict[str, str],
    note_title: str = "",
    duplicate_titles: set[str] | None = None,
) -> tuple[str, int, int]:
    """
    Replace evernote:///view/... links in ENML with Google Doc URLs.

    Resolved links become <a href="https://docs.google.com/...">Title</a>.
    Unresolved links become plain text: [link to "Title" not resolved].

    Returns (rewritten_enml, resolved_count, unresolved_count).
    """
    resolved = 0
    unresolved = 0

    def _replace(m: re.Match) -> str:
        nonlocal resolved, unresolved
        inner = m.group(1)
        title = _anchor_title(inner)
        doc_id = title_to_doc_id.get(title)
        if doc_id:
            resolved += 1
            if duplicate_titles and title in duplicate_titles:
                _log.warning(
                    "note %r: inter-note link to %r resolved but title has duplicates — may point to wrong doc",
                    note_title,
                    title,
                )
            return f'<a href="{_GDOC_URL.format(doc_id)}">{inner}</a>'
        else:
            unresolved += 1
            _log.warning(
                "note %r: inter-note link to %r not resolved",
                note_title,
                title,
            )
            label = title if title else "unknown"
            return f'[link to "{label}" not resolved]'

    rewritten = _RE_INTERLINK.sub(_replace, enml)
    return rewritten, resolved, unresolved


@dataclass
class DeferredNote:
    """Lightweight cache of state needed to rewrite inter-note links in pass 2."""
    title: str
    doc_id: str
    enml: str
    hash_to_img_url: dict[str, str]
    hash_to_link: dict[str, tuple[str, str]]
    source_url: str | None
    modified_time: datetime | None
    image_file_ids: list[str] = field(default_factory=list)
    include_tags: bool = True
    tags: list[str] = field(default_factory=list)

"""
Shared ENML-to-HTML sanitization used by both local (DOCX) and Drive output.

The only intentional difference between the two output paths is *how* <en-media>
tags are replaced (inline base64 vs Drive URLs).  Everything else — stripping
XML/DOCTYPE declarations, removing <en-crypt> blocks, converting checkboxes,
stripping external images — is identical and lives here.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Callable

from .display import rtl_display

_log = logging.getLogger(__name__)

# Compiled patterns used in sanitize_enml
_RE_XML_DECL = re.compile(r"<\?xml[^?]*\?>\s*")
_RE_DOCTYPE = re.compile(r"<!DOCTYPE[^>]*>\s*", re.IGNORECASE)
_RE_EN_NOTE = re.compile(r"</?en-note\b[^>]*>")
_RE_EN_MEDIA_SC = re.compile(r"<en-media\b[^>]*/>")
_RE_EN_MEDIA_PAIRED = re.compile(r"<en-media\b[^>]*>.*?</en-media>", re.DOTALL)
_RE_EN_CRYPT = re.compile(r"<en-crypt\b[^>]*>.*?</en-crypt>", re.DOTALL)
_RE_TODO_CHECKED = re.compile(r'<en-todo\b[^>]*checked="true"[^>]*/>')
_RE_TODO_UNCHECKED = re.compile(r"<en-todo\b[^>]*/?>")
_RE_EXT_IMG_FIND = re.compile(r'<img\b[^>]*\bsrc="(https?://[^"]*)"[^>]*/?>',
                               re.IGNORECASE)
_RE_EXT_IMG_SC = re.compile(r'<img\b[^>]*\bsrc="https?://[^"]*"[^>]*/>', re.IGNORECASE)
_RE_EXT_IMG_OPEN = re.compile(r'<img\b[^>]*\bsrc="https?://[^"]*"[^>]*>', re.IGNORECASE)

_RE_HASH = re.compile(r'\bhash="([0-9a-fA-F]+)"')
_RE_MIME = re.compile(r'\btype="([^"]+)"')


def enml_to_text(enml: str) -> str:
    """Extract plain text from ENML by stripping all tags."""
    if not enml:
        return ""
    text = re.sub(r"<[^>]+>", " ", enml)
    return " ".join(text.split())


def parse_media_tag(tag: str) -> tuple[str | None, str | None]:
    """Return (hash, mime_type) extracted from an <en-media> tag string."""
    h = _RE_HASH.search(tag)
    t = _RE_MIME.search(tag)
    return (h.group(1) if h else None, t.group(1) if t else None)


def _strip_external_images(html: str, title: str) -> str:
    external_imgs = _RE_EXT_IMG_FIND.findall(html)
    if external_imgs:
        note_label = f" {rtl_display(title)!r}" if title else ""
        _log.warning("note%s: %d external image(s) skipped (not embeddable)",
                     note_label, len(external_imgs))
        print(
            f"WARNING:{note_label}: {len(external_imgs)} external image(s) skipped"
            " (not embeddable)",
            file=sys.stderr,
            flush=True,
        )
    html = _RE_EXT_IMG_SC.sub("", html)
    html = _RE_EXT_IMG_OPEN.sub("", html)
    return html


def sanitize_enml(
    enml: str,
    replace_media: Callable[[re.Match], str],
    *,
    title: str = "",
) -> str:
    """Convert ENML to clean HTML, delegating <en-media> handling to replace_media.

    Strips XML/DOCTYPE declarations, the <en-note> wrapper, <en-crypt> blocks,
    converts checkboxes to text markers, and removes external <img> tags.
    """
    html = _RE_XML_DECL.sub("", enml.strip())
    html = _RE_DOCTYPE.sub("", html)
    html = _RE_EN_NOTE.sub("", html)
    html = _RE_EN_MEDIA_SC.sub(replace_media, html)
    html = _RE_EN_MEDIA_PAIRED.sub(replace_media, html)
    html = _RE_EN_CRYPT.sub("", html)
    html = _RE_TODO_CHECKED.sub("[x]\u00a0", html)
    html = _RE_TODO_UNCHECKED.sub("[\u00a0]\u00a0", html)
    html = _strip_external_images(html, title)
    return html.strip()

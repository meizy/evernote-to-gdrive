"""
Convert ENML to HTML suitable for Google Drive's import converter.
"""

from __future__ import annotations

import re

from ._enml import sanitize_enml, parse_media_tag, source_url_html
from .classifier import IMAGE_MAX_WIDTH_PX


def enml_to_gdoc_html(
    enml: str,
    hash_to_image_url: dict[str, str],
    hash_to_attachment_link: dict[str, tuple[str, str]],
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
    def _replace(m: re.Match) -> str:
        h, _ = parse_media_tag(m.group(0))
        if not h:
            return ""
        if h in hash_to_image_url:
            return (f'<p style="text-align:center">'
                    f'<img src="{hash_to_image_url[h]}" width="{IMAGE_MAX_WIDTH_PX}px"/></p>')
        if h in hash_to_attachment_link:
            filename, url = hash_to_attachment_link[h]
            return f'<p><a href="{url}">[{filename}]</a></p>'
        return ""

    html = sanitize_enml(enml, _replace, title=title)
    html = f"<div>{html}</div>"
    if source_url:
        html = html.replace("<div>", f"<div>{source_url_html(source_url)}", 1)
    return html.encode("utf-8")

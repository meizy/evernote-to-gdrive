"""
Synthetic ENEX file builder for e2e tests.

Generates valid ENEX XML using f-strings (not xml.etree) because ENEX
<content> elements must use CDATA, which xml.etree doesn't support natively.
"""

import base64
import hashlib
import textwrap
from pathlib import Path

# ── Binary assets ─────────────────────────────────────────────────────────────

_ASSETS = Path(__file__).parent.parent / "assets"
TEST_PNG = (_ASSETS / "image1.png").read_bytes()
TEST_JPEG = (_ASSETS / "image2.jpg").read_bytes()
TEST_PDF = (_ASSETS / "pdf1.pdf").read_bytes()
TEST_PDF_2 = (_ASSETS / "pdf2.pdf").read_bytes()

# ── XML builders ──────────────────────────────────────────────────────────────

def make_resource(data: bytes, mime: str, filename: str) -> tuple[str, str]:
    """Return (resource_xml, md5_hex). md5_hex is used in <en-media hash="">."""
    b64 = base64.b64encode(data).decode()
    md5 = hashlib.md5(data).hexdigest()
    xml = textwrap.dedent(f"""\
        <resource>
          <data encoding="base64">{b64}</data>
          <mime>{mime}</mime>
          <resource-attributes><file-name>{filename}</file-name></resource-attributes>
        </resource>""")
    return xml, md5


def _en_media(md5: str, mime: str) -> str:
    return f'<en-media hash="{md5}" type="{mime}"/>'


def make_note(
    title: str,
    body_html: str,
    resources: list[tuple[bytes, str, str]] | None = None,
    tags: list[str] | None = None,
    source_url: str | None = None,
) -> str:
    """
    Build a <note> XML string.

    resources: list of (data, mime, filename)
    body_html: inner content for <en-note>. Empty string → attachment-only note
               (only <en-media> tags in body, no text wrapper).
    """
    resource_xmls = []
    media_tags = []
    for data, mime, filename in (resources or []):
        res_xml, md5 = make_resource(data, mime, filename)
        resource_xmls.append(res_xml)
        media_tags.append(_en_media(md5, mime))

    if body_html:
        en_note_body = f"<div>{body_html}</div>" + "".join(media_tags)
    else:
        en_note_body = "".join(media_tags)

    content_cdata = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/en-note.dtd">\n'
        f'<en-note>{en_note_body}</en-note>'
    )

    source_block = ""
    if source_url:
        source_block = f"<note-attributes><source-url>{source_url}</source-url></note-attributes>"

    tag_block = "".join(f"<tag>{t}</tag>" for t in (tags or []))
    resources_block = "\n".join(resource_xmls)

    return textwrap.dedent(f"""\
        <note>
          <title>{title}</title>
          <created>20240101T000000Z</created>
          <updated>20240101T000000Z</updated>
          {source_block}
          <content><![CDATA[{content_cdata}]]></content>
          {resources_block}
          {tag_block}
        </note>""")


def make_enex(notes: list[str]) -> str:
    """Wrap note XML strings in the <en-export> envelope."""
    notes_block = "\n".join(notes)
    # No textwrap.dedent here — the XML declaration must start at column 0.
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE en-export SYSTEM "http://xml.evernote.com/pub/evernote-export4.dtd">\n'
        f'<en-export>\n{notes_block}\n</en-export>'
    )


def write_enex(path: Path, notes: list[str]) -> None:
    path.write_text(make_enex(notes), encoding="utf-8")

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
from typing import Callable
from lxml import html as lxml_html

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
    html = _strip_external_images(html, title)  # strip before replace_media inserts Drive URLs
    html = _RE_EN_MEDIA_SC.sub(replace_media, html)
    html = _RE_EN_MEDIA_PAIRED.sub(replace_media, html)
    html = _RE_EN_CRYPT.sub("", html)
    html = _RE_TODO_CHECKED.sub("[x]\u00a0", html)
    html = _RE_TODO_UNCHECKED.sub("[\u00a0]\u00a0", html)
    html = re.sub(r'^(\s*<div>\s*<br\s*/?>\s*</div>\s*)+', '', html)
    html = html.strip()
    if "<li" in html.lower():
        html = normalize_evernote_list_html(html)
    return html


def _is_whitespace_text(text: str | None) -> bool:
    return not text or not text.replace("\xa0", " ").strip()


def _node_effectively_empty(node) -> bool:
    if not _is_whitespace_text(node.text):
        return False
    for child in node:
        if child.tag == "br":
            if not _is_whitespace_text(child.tail):
                return False
            continue
        if not _node_effectively_empty(child):
            return False
        if not _is_whitespace_text(child.tail):
            return False
    return True


def _trim_trailing_breaks(node) -> None:
    while len(node):
        last = node[-1]
        if not _is_whitespace_text(last.tail):
            break
        if last.tag == "br" or _node_effectively_empty(last):
            node.remove(last)
            continue
        _trim_trailing_breaks(last)
        if _node_effectively_empty(last) and _is_whitespace_text(last.tail):
            node.remove(last)
            continue
        break


def _node_has_trailing_break_candidate(node) -> bool:
    if not len(node):
        return False
    last = node[-1]
    if not _is_whitespace_text(last.tail):
        return False
    if last.tag == "br":
        return True
    return _node_effectively_empty(last)


def _is_meaningful_list_item(node) -> bool:
    return node.tag == "li" and not _is_whitespace_text("".join(node.itertext()))


def _is_bare_url_item(node) -> bool:
    text = " ".join("".join(node.itertext()).split())
    return bool(re.fullmatch(r"https?://\S+", text))


def _append_inline_content(dest, src) -> None:
    if src.text:
        if len(dest):
            last = dest[-1]
            last.tail = (last.tail or "") + src.text
        else:
            dest.text = (dest.text or "") + src.text
    for child in list(src):
        src.remove(child)
        dest.append(child)


def _is_list_only_wrapper(node) -> bool:
    if node.tag not in {"div", "p"} or not _is_whitespace_text(node.text):
        return False
    if not len(node):
        return False
    saw_list = False
    for child in node:
        if child.tag in {"ul", "ol"}:
            saw_list = True
        elif _node_effectively_empty(child):
            pass
        else:
            return False
        if not _is_whitespace_text(child.tail):
            return False
    return saw_list


def _unwrap_element(node) -> None:
    parent = node.getparent()
    if parent is None:
        return
    idx = parent.index(node)
    insert_at = idx
    if node.text:
        prev = parent[idx - 1] if idx > 0 else None
        if prev is not None:
            prev.tail = (prev.tail or "") + node.text
        else:
            parent.text = (parent.text or "") + node.text
    for child in list(node):
        node.remove(child)
        parent.insert(insert_at, child)
        insert_at += 1
    if node.tail:
        if insert_at > 0:
            prev = parent[insert_at - 1]
            prev.tail = (prev.tail or "") + node.tail
        else:
            parent.text = (parent.text or "") + node.tail
    parent.remove(node)


def _normalize_list_style_none_wrappers(root) -> bool:
    changed = False
    for list_node in list(root.iter("ul", "ol")):
        children = list(list_node)
        for idx, child in enumerate(children):
            if child.tag != "li" or idx == 0:
                continue
            style = (child.get("style") or "").replace(" ", "").lower()
            if "list-style:none" not in style:
                continue
            nested_lists = [gc for gc in child if gc.tag in {"ul", "ol"}]
            if len(nested_lists) != 1 or not _is_whitespace_text(child.text):
                continue
            nested_list = nested_lists[0]
            nested_items = [gc for gc in nested_list if gc.tag == "li"]
            if not nested_items:
                continue
            meaningful_items = []
            for nested_li in nested_items:
                _trim_trailing_breaks(nested_li)
                if not _is_whitespace_text("".join(nested_li.itertext())):
                    meaningful_items.append(nested_li)
                    continue
                if any(not _node_effectively_empty(c) for c in nested_li):
                    meaningful_items.append(nested_li)
            prev = children[idx - 1]
            if len(meaningful_items) == 1 and _is_bare_url_item(meaningful_items[0]):
                for nested_li in meaningful_items:
                    prev.append(lxml_html.Element("br"))
                    _append_inline_content(prev, nested_li)
                list_node.remove(child)
                _trim_trailing_breaks(prev)
                changed = True
                continue

            for nested_li in list(nested_items):
                if nested_li not in meaningful_items:
                    nested_list.remove(nested_li)
            if len(nested_list) == 0:
                list_node.remove(child)
                changed = True
                continue
            child.remove(nested_list)
            prev.append(nested_list)
            list_node.remove(child)
            _trim_trailing_breaks(prev)
            changed = True
    return changed


def _unwrap_simple_list_item_blocks(root) -> bool:
    changed = False
    for li in list(root.iter("li")):
        block_children = [child for child in li if child.tag in {"div", "p"}]
        if not block_children:
            continue
        if any(child.tag not in {"div", "p", "ul", "ol"} for child in li):
            continue
        if len(block_children) != sum(1 for child in li if child.tag in {"div", "p"}):
            continue
        for block in block_children:
            _unwrap_element(block)
            changed = True
        _trim_trailing_breaks(li)
    return changed


def _collect_list_terminal_gap_targets(root) -> list:
    targets = []
    for list_node in root.iter("ul", "ol"):
        items = [child for child in list_node if _is_meaningful_list_item(child)]
        if not items:
            continue
        last_item = items[-1]
        if not _node_has_trailing_break_candidate(last_item):
            continue
        parent = list_node.getparent()
        if parent is None:
            continue
        siblings = list(parent)
        idx = siblings.index(list_node)
        next_sibling = None
        for sibling in siblings[idx + 1:]:
            if sibling.tag in {"ul", "ol"}:
                next_sibling = sibling
                break
            if sibling.tag in {"div", "p"} and _node_effectively_empty(sibling):
                continue
            next_sibling = sibling
            break
        if next_sibling is None or next_sibling.tag in {"ul", "ol"}:
            continue
        targets.append(list_node)
    return targets


def _insert_list_terminal_gaps(targets: list) -> bool:
    changed = False
    for list_node in targets:
        parent = list_node.getparent()
        if parent is None:
            continue
        gap = lxml_html.Element("div")
        gap.append(lxml_html.Element("br"))
        parent.insert(parent.index(list_node) + 1, gap)
        changed = True
    return changed


def normalize_evernote_list_html(html: str) -> str:
    """Normalize Evernote-specific list HTML artifacts before downstream import."""
    root = lxml_html.fragment_fromstring(html, create_parent=True)
    changed = False
    list_gap_targets = _collect_list_terminal_gap_targets(root)
    for node in root.iter():
        if node.tag in {"li", "div", "p"}:
            before = lxml_html.tostring(node, encoding="unicode")
            _trim_trailing_breaks(node)
            if before != lxml_html.tostring(node, encoding="unicode"):
                changed = True
    if _insert_list_terminal_gaps(list_gap_targets):
        changed = True
    if _normalize_list_style_none_wrappers(root):
        changed = True
    if _unwrap_simple_list_item_blocks(root):
        changed = True
    for node in list(root.iter("div", "p")):
        if _is_list_only_wrapper(node):
            _unwrap_element(node)
            changed = True
    if not changed:
        return html
    return "".join(
        lxml_html.tostring(child, encoding="unicode")
        for child in root
    )


def source_url_html(url: str) -> str:
    """Return an HTML paragraph linking to the note's source URL."""
    return f'<p>Source: <a href="{url}">{url}</a></p>'

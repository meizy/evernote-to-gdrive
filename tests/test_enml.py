"""Tests for the shared ENML sanitization module."""

import re

import pytest

from evernote_to_google._enml import parse_media_tag, sanitize_enml


# ── parse_media_tag ───────────────────────────────────────────────────────────

def test_parse_media_tag_both():
    tag = '<en-media hash="abc123DEF456" type="image/png"/>'
    h, mime = parse_media_tag(tag)
    assert h == "abc123DEF456"
    assert mime == "image/png"


def test_parse_media_tag_no_hash():
    tag = '<en-media type="image/png"/>'
    h, mime = parse_media_tag(tag)
    assert h is None
    assert mime == "image/png"


def test_parse_media_tag_no_type():
    tag = '<en-media hash="abc123"/>'
    h, mime = parse_media_tag(tag)
    assert h == "abc123"
    assert mime is None


def test_parse_media_tag_uppercase_hex():
    tag = '<en-media hash="DEADBEEF1234" type="application/pdf"/>'
    h, _ = parse_media_tag(tag)
    assert h == "DEADBEEF1234"


# ── sanitize_enml — preamble stripping ───────────────────────────────────────

def _noop(m: re.Match) -> str:
    return ""


def test_strips_xml_declaration():
    enml = '<?xml version="1.0" encoding="UTF-8"?><en-note>hello</en-note>'
    result = sanitize_enml(enml, _noop)
    assert "<?xml" not in result
    assert "hello" in result


def test_strips_doctype_case_insensitive():
    enml = '<!doctype en-note SYSTEM "...">\n<en-note>hi</en-note>'
    result = sanitize_enml(enml, _noop)
    assert "<!doctype" not in result.lower()
    assert "hi" in result


def test_strips_en_note_tags():
    enml = '<en-note style="color:red">body</en-note>'
    result = sanitize_enml(enml, _noop)
    assert "<en-note" not in result
    assert "</en-note>" not in result
    assert "body" in result


# ── sanitize_enml — en-media replacement ─────────────────────────────────────

def test_self_closing_en_media():
    called_with = []

    def replacer(m: re.Match) -> str:
        called_with.append(m.group(0))
        return "[IMAGE]"

    enml = '<en-note><en-media hash="aaa" type="image/png"/></en-note>'
    result = sanitize_enml(enml, replacer)
    assert result == "[IMAGE]"
    assert len(called_with) == 1
    assert 'hash="aaa"' in called_with[0]


def test_paired_en_media():
    """Paired <en-media>content</en-media> must be replaced (not leave orphans)."""
    called_with = []

    def replacer(m: re.Match) -> str:
        called_with.append(m.group(0))
        return "[MEDIA]"

    enml = '<en-note><en-media hash="bbb" type="image/jpeg">caption</en-media></en-note>'
    result = sanitize_enml(enml, replacer)
    assert result == "[MEDIA]"
    assert "</en-media>" not in result
    assert len(called_with) == 1


def test_mixed_case_hash_in_en_media():
    """Hash attribute with uppercase hex must be passed through to the callback."""
    seen = []

    def replacer(m: re.Match) -> str:
        h, _ = parse_media_tag(m.group(0))
        seen.append(h)
        return ""

    enml = '<en-note><en-media hash="ABCDEF123456" type="image/png"/></en-note>'
    sanitize_enml(enml, replacer)
    assert seen == ["ABCDEF123456"]


# ── sanitize_enml — en-crypt removal ─────────────────────────────────────────

def test_strips_en_crypt():
    enml = '<en-note>text<en-crypt hint="pass">encrypted</en-crypt>after</en-note>'
    result = sanitize_enml(enml, _noop)
    assert "<en-crypt" not in result
    assert "encrypted" not in result
    assert "after" in result


# ── sanitize_enml — checkbox conversion ──────────────────────────────────────

def test_checked_todo():
    enml = '<en-note><en-todo checked="true"/> Buy milk</en-note>'
    result = sanitize_enml(enml, _noop)
    assert "[x]\u00a0 Buy milk" in result


def test_unchecked_todo_self_closing():
    enml = '<en-note><en-todo/> Buy milk</en-note>'
    result = sanitize_enml(enml, _noop)
    assert "[\u00a0]\u00a0 Buy milk" in result


def test_unchecked_todo_open_tag():
    """Non-self-closing <en-todo> (without slash) must also be replaced."""
    enml = '<en-note><en-todo> Buy milk</en-note>'
    result = sanitize_enml(enml, _noop)
    assert "[\u00a0]\u00a0 Buy milk" in result
    assert "<en-todo>" not in result


# ── sanitize_enml — external image stripping ─────────────────────────────────

def test_strips_external_img_self_closing(caplog):
    import logging
    enml = '<en-note><img src="https://example.com/pic.jpg"/></en-note>'
    with caplog.at_level(logging.WARNING, logger="evernote_to_google._enml"):
        result = sanitize_enml(enml, _noop)
    assert "<img" not in result
    assert "WARNING" in caplog.text
    assert "1 external image(s)" in caplog.text


def test_strips_external_img_open_tag(caplog):
    import logging
    enml = '<en-note><img src="https://example.com/pic.jpg"></en-note>'
    with caplog.at_level(logging.WARNING, logger="evernote_to_google._enml"):
        result = sanitize_enml(enml, _noop)
    assert "<img" not in result
    assert "1 external image(s)" in caplog.text


def test_warning_includes_title(caplog):
    import logging
    enml = '<en-note><img src="https://example.com/x.jpg"/></en-note>'
    with caplog.at_level(logging.WARNING, logger="evernote_to_google._enml"):
        sanitize_enml(enml, _noop, title="My Note")
    assert "'My Note'" in caplog.text


def test_no_warning_without_external_imgs(caplog):
    import logging
    enml = '<en-note>plain text</en-note>'
    with caplog.at_level(logging.WARNING, logger="evernote_to_google._enml"):
        sanitize_enml(enml, _noop)
    assert not caplog.records


# ── sanitize_enml — full round trip ──────────────────────────────────────────

def test_full_roundtrip():
    enml = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE en-note SYSTEM "...">'
        '<en-note>'
        '<en-todo checked="true"/>Buy milk'
        '<en-media hash="abc" type="image/png"/>'
        '<en-crypt hint="x">secret</en-crypt>'
        '</en-note>'
    )

    def replacer(m: re.Match) -> str:
        return "[IMG]"

    result = sanitize_enml(enml, replacer, title="Shopping")
    assert "<?xml" not in result
    assert "DOCTYPE" not in result
    assert "<en-note" not in result
    assert "[x]\u00a0Buy milk" in result
    assert "[IMG]" in result
    assert "secret" not in result

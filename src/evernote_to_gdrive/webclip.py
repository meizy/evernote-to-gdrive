"""
Web clip rendering: Readability.js extraction + Playwright PDF/HTML output.

Provides WebClipRenderer which owns the Playwright browser lifecycle.
Create once per migration run; browser is launched lazily on first use.
"""

from __future__ import annotations

import logging
import urllib.request
from datetime import datetime
from pathlib import Path

from .classifier import _is_rtl
from ._docx_builder import attachment_hash_map, build_html
from ._runtime_paths import is_frozen, repo_root_or_none, user_data_dir
from .parser import Note

_log = logging.getLogger(__name__)


# ── constants ──────────────────────────────────────────────────────────────────

_READABILITY_URL = "https://cdn.jsdelivr.net/npm/@mozilla/readability@0.6.0/Readability.js"


def _readability_cache_path() -> Path:
    if is_frozen():
        return user_data_dir() / ".cache" / "Readability.js"
    return repo_root_or_none() / ".cache" / "Readability.js"


_READABILITY_CACHE = _readability_cache_path()


# ── helpers ────────────────────────────────────────────────────────────────────

def _ensure_readability() -> str:
    if not _READABILITY_CACHE.exists():
        _READABILITY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        print("  Downloading Readability.js from jsDelivr...")
        urllib.request.urlretrieve(_READABILITY_URL, _READABILITY_CACHE)
    return _READABILITY_CACHE.read_text(encoding="utf-8")


def _format_date(dt: datetime) -> str:
    local = dt.astimezone()
    month = local.strftime("%b")
    day = local.day
    year = local.year
    hour = local.hour % 12 or 12
    minute = local.strftime("%M")
    ampm = local.strftime("%p")
    tz = local.strftime("%Z")
    return f"{month} {day}, {year} at {hour}:{minute} {ampm} {tz}"


def _reader_css(dark: bool, rtl: bool = False) -> str:
    if dark:
        bg, bg2, text, heading, link = "#3a3a3c", "#2a2a2c", "#e8e8e8", "#f2f2f7", "#6ab0f5"
        bq_border, bq_text, hr_color, meta_color = "#555", "#aaa", "#505052", "#888"
    else:
        bg, bg2, text, heading, link = "#ffffff", "#f5f5f5", "#222222", "#111111", "#1174c7"
        bq_border, bq_text, hr_color, meta_color = "#ccc", "#555", "#dddddd", "#888"
    return f"""
<style>
  html {{ background-color: {bg}; }}
  body {{
    font-family: Georgia, serif;
    font-size: 14px;
    line-height: 1.6;
    max-width: 540px;
    margin: 0 auto;
    padding: 2cm 1.5cm;
    color: {text};
    {"direction: rtl; text-align: right;" if rtl else ""}
  }}
  h1, h2, h3 {{ font-family: sans-serif; line-height: 1.3; color: {heading}; }}
  img {{ max-width: 100%; height: auto; display: block; margin: 1em 0; }}
  figure {{ margin: 1em 0; }}
  figure img {{ margin: 0; }}
  figcaption {{ font-size: 0.82em; color: {meta_color}; font-family: sans-serif; margin-top: 0.3em; }}
  a {{ color: {link}; }}
  p {{ margin: 0 0 1em; }}
  blockquote {{ border-left: 3px solid {bq_border}; margin: 1em 0; padding-left: 1em; color: {bq_text}; }}
  pre, code {{ font-family: monospace; background: {bg2}; padding: 2px 4px; }}
  .reader-header h1 {{ margin: 0 0 0.3em; font-size: 1.8em; }}
  .reader-header .excerpt {{ font-style: italic; color: {bq_text}; font-size: 0.95em; margin: 0 0 0.4em; }}
  .reader-header .date {{ color: {meta_color}; font-size: 0.85em; font-family: sans-serif; margin: 0; }}
  .reader-header hr {{ border: none; border-top: 1px solid {hr_color}; margin: 1em 0 1.5em; }}
  .source-bar {{ font-family: sans-serif; font-size: 0.78em; color: {meta_color}; margin-bottom: 1.5em; word-break: break-all; }}
  .source-bar a {{ color: {meta_color}; text-decoration: none; }}
</style>
"""


def _build_clip_html(note: Note) -> str:
    hash_map = attachment_hash_map(note.attachments)
    body = build_html(note, hash_map, include_tags=False, include_source_url=False)
    return f"<html><head><title>{note.title}</title></head><body>{body}</body></html>"


def _build_header_html(title: str, subtitle: str | None, published_time: str | None,
                       note_created: datetime | None, source_url: str | None) -> str:
    parts = []
    if source_url or note_created:
        parts.append("<div class='source-bar'>")
        if source_url:
            parts.append(f"<a href='{source_url}'>{source_url}</a><br>")
        if note_created:
            parts.append(f"Saved: {_format_date(note_created)}")
        parts.append("</div>")
    parts.append(f"<div class='reader-header'><h1>{title}</h1>")
    if subtitle:
        parts.append(f"<p class='excerpt'>{subtitle}</p>")
    dt: datetime | None = None
    if published_time:
        try:
            dt = datetime.fromisoformat(published_time.replace("Z", "+00:00"))
        except ValueError:
            pass
    if dt:
        parts.append(f"<p class='date'>{_format_date(dt)}</p>")
    parts.append("<hr></div>")
    return "".join(parts)


def _extract_subtitle(page) -> str | None:
    return page.evaluate("""() => {
        const h1 = document.querySelector('h1');
        if (!h1) return null;
        const allP = Array.from(document.querySelectorAll('p'));
        let pastH1 = false;
        for (const p of allP) {
            if (!pastH1) { if (h1.compareDocumentPosition(p) & 4) pastH1 = true; else continue; }
            const style = p.getAttribute('style') || '';
            const m = style.match(/font-size:\\s*([\\d.]+)em/);
            if (m && parseFloat(m[1]) >= 1.3) {
                const text = p.textContent.trim();
                if (text.length > 10) return text;
            }
        }
        return null;
    }""")


def _remove_hidden_elements(page, note_title: str) -> None:
    page.evaluate("""(noteTitle) => {
        document.querySelectorAll('[style]').forEach(el => {
            const s = el.getAttribute('style') || '';
            if (!/display\\s*:\\s*none/i.test(s)) return;
            const h1 = el.querySelector('h1');
            if (!h1) return;
            const h1Text = h1.textContent.trim().toLowerCase();
            if (!noteTitle || !h1Text.includes(noteTitle.toLowerCase().substring(0, 30))) {
                el.remove();
            }
        });
    }""", note_title)
    page.evaluate("document.querySelectorAll('[style]').forEach(el => el.removeAttribute('style'))")


def _normalize_figures(page) -> None:
    page.evaluate("""
        [...document.querySelectorAll('div')].reverse().forEach(div => {
            if (!div.isConnected) return;
            const children = Array.from(div.children);
            const imgs = children.filter(c => c.tagName === 'IMG');
            if (imgs.length !== 1) return;
            const nonImg = children.filter(c => c.tagName !== 'IMG');
            if (nonImg.length > 1) return;
            const fig = document.createElement('figure');
            fig.appendChild(imgs[0]);
            if (nonImg.length === 1) {
                const other = nonImg[0];
                if (!['A', 'SPAN', 'FIGCAPTION', 'P'].includes(other.tagName)) return;
                const text = other.textContent.trim();
                if (text.length > 200) return;
                const cap = document.createElement('figcaption');
                cap.textContent = text;
                fig.appendChild(cap);
            }
            div.replaceWith(fig);
        });
    """)
    page.evaluate("""
        document.querySelectorAll('p').forEach(p => {
            if (p.children.length !== 1 || p.children[0].tagName !== 'IMG') return;
            const next = p.nextElementSibling;
            if (!next || next.tagName !== 'P') return;
            if (next.querySelector('img, h1, h2, h3, ul, ol')) return;
            const text = next.textContent.trim();
            if (!text || text.length > 200) return;
            const fig = document.createElement('figure');
            fig.appendChild(p.children[0]);
            const cap = document.createElement('figcaption');
            cap.textContent = text;
            fig.appendChild(cap);
            next.remove();
            p.replaceWith(fig);
        });
    """)


def _remove_link_lists(page) -> None:
    page.evaluate("""
        document.querySelectorAll('ul, ol').forEach(list => {
            const items = Array.from(list.querySelectorAll('li'));
            if (items.length > 0 && items.every(li => {
                const text = li.textContent.trim();
                const links = li.querySelectorAll('a');
                return links.length > 0 && text === links[0].textContent.trim();
            })) {
                const prev = list.previousElementSibling;
                if (prev && /^h[1-6]$/i.test(prev.tagName)) prev.remove();
                list.remove();
            }
        });
    """)


def _extract_readability(browser, html: str, readability_js: str,
                         note_title: str = "", note_created: datetime | None = None,
                         dark: bool = False, source_url: str | None = None) -> str | None:
    page = browser.new_page()
    page.set_content(html, wait_until="domcontentloaded")

    subtitle = _extract_subtitle(page)
    _remove_hidden_elements(page, note_title)
    _normalize_figures(page)
    _remove_link_lists(page)

    page.evaluate(readability_js)
    article = page.evaluate("""() => {
        const a = new Readability(document).parse();
        return a ? {title: a.title, publishedTime: a.publishedTime, content: a.content} : null;
    }""")
    page.close()

    if not article:
        return None

    title = note_title or article.get("title")
    header = _build_header_html(title, subtitle, article.get("publishedTime"), note_created, source_url)
    body = header + article["content"]
    rtl = _is_rtl(note_title or article.get("title", ""))
    dir_attr = ' dir="rtl"' if rtl else ""
    return f"<html{dir_attr}><head>{_reader_css(dark, rtl=rtl)}</head><body>{body}</body></html>"


def _render_to_pdf_bytes(browser, article_html: str) -> bytes:
    import tempfile, os
    page = browser.new_page()
    page.set_content(article_html, wait_until="domcontentloaded")
    page.emulate_media(media="print")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        tmp = f.name
    try:
        page.pdf(
            path=tmp,
            format="A4",
            print_background=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
        )
        return Path(tmp).read_bytes()
    finally:
        page.close()
        os.unlink(tmp)


# ── public API ─────────────────────────────────────────────────────────────────

class WebClipRenderer:
    """Owns the Playwright browser lifecycle for web clip rendering.

    Create once per migration run. Browser is launched lazily on first use.
    Call close() when done (or use as a context manager).
    """

    def __init__(self, dark: bool = False) -> None:
        self._dark = dark
        self._pw = None
        self._browser = None
        self._readability_js: str | None = None
        self._unavailable = False

    def _ensure_browser(self) -> bool:
        """Return False (and warn once) if browser can't be launched; True if ready."""
        if self._browser is not None:
            return True
        if self._unavailable:
            return False
        try:
            self._readability_js = _ensure_readability()
        except Exception as exc:
            self._unavailable = True
            _log.warning(
                "Could not fetch Readability.js — web clips will be migrated as regular notes. (%s)", exc
            )
            return False
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch()
            return True
        except Exception as exc:
            self._unavailable = True
            _log.warning(
                "Chromium unavailable — web clips will be migrated as regular notes. "
                "Run 'evernote-to-gdrive install-browsers' to enable high-fidelity rendering.\n  (%s)",
                str(exc).splitlines()[0],
            )
            return False

    def _extract_article(self, note: Note) -> str | None:
        if not self._ensure_browser():
            return None
        html = _build_clip_html(note)
        return _extract_readability(
            self._browser, html, self._readability_js,
            note_title=note.title, note_created=note.created,
            dark=self._dark, source_url=note.source_url,
        )

    def render_pdf(self, note: Note) -> bytes | None:
        """Extract article via Readability, render to PDF, return bytes. Returns None if Readability can't parse."""
        article_html = self._extract_article(note)
        if article_html is None:
            return None
        return _render_to_pdf_bytes(self._browser, article_html)

    def render_html(self, note: Note) -> str | None:
        """Extract article via Readability, return styled HTML string. Returns None if Readability can't parse."""
        return self._extract_article(note)

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

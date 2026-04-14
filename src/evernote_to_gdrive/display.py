"""
Display utilities for terminal output.
"""

from __future__ import annotations

import unicodedata

_RTL_BIDI = frozenset(("R", "AL", "AN"))


def notebook_path(stack: str | None, notebook: str) -> str:
    """Format notebook as a plain stack/notebook path (no RTL reversal — for bidi-aware apps like Pages)."""
    return f"{stack}/{notebook}" if stack else notebook


def format_notebook(stack: str | None, notebook: str) -> str:
    """Render a notebook for terminal display, prefixed by its stack if any."""
    if stack:
        return f"{rtl_display(stack)}/{rtl_display(notebook)}"
    return rtl_display(notebook)


def rtl_display(name: str) -> str:
    """Reverse name for terminal display if its first word consists of RTL characters.

    Uses Unicode bidi categories on the first word only — suitable for log output
    where mixed-script strings should read left-to-right. For document-level RTL
    detection (paragraph direction, DOCX bidi) use classifier._is_rtl() instead.
    """
    first_word = name.split()[0] if name.split() else name
    if all(unicodedata.bidirectional(c) in _RTL_BIDI for c in first_word):
        return name[::-1]
    return name

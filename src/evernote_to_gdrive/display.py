"""
Display utilities for terminal output.
"""

from __future__ import annotations

import os
import unicodedata

_RTL_BIDI = frozenset(("R", "AL", "AN"))

# Terminals known to run a full BiDi engine (UBA) on rendered lines.
# These need Unicode isolate controls; a pre-reversed string would be double-reversed.
# All other terminals (VSCode/xterm.js, Windows conhost, iTerm2, …) render in logical
# order, so the old visual-reversal trick is still needed there.
_BIDI_TERMINALS = frozenset(("Apple_Terminal",))

_terminal_has_bidi: bool = os.environ.get("TERM_PROGRAM", "") in _BIDI_TERMINALS


def set_bidi(enabled: bool) -> None:
    """Override the auto-detected BiDi terminal setting (e.g. from --bidi CLI flag)."""
    global _terminal_has_bidi
    _terminal_has_bidi = enabled


def notebook_path(stack: str | None, notebook: str) -> str:
    """Format notebook as a plain stack/notebook path (no RTL reversal — for bidi-aware apps like Pages)."""
    return f"{stack}/{notebook}" if stack else notebook


def format_notebook(stack: str | None, notebook: str) -> str:
    """Render a notebook for terminal display, prefixed by its stack if any."""
    if stack:
        return f"{rtl_display(stack)}/{rtl_display(notebook)}"
    return rtl_display(notebook)


def rtl_display(name: str) -> str:
    """Render name correctly for the current terminal if it contains RTL characters.

    - BiDi-capable terminals (Terminal.app): wrap in LRM + LRI…PDI so the terminal's
      own BiDi engine reorders glyphs and the paragraph direction stays LTR.
    - All other terminals (VSCode, conhost, …): physically reverse the characters so
      they appear in the correct visual order without any BiDi engine.

    For document-level RTL detection (paragraph direction, DOCX bidi) use
    classifier._is_rtl() instead.
    """
    if any(unicodedata.bidirectional(c) in _RTL_BIDI for c in name):
        if _terminal_has_bidi:
            return f"\u200e\u2066{name}\u2069"
        return name[::-1]
    return name

"""
Display utilities for terminal output.
"""

from __future__ import annotations

import unicodedata

_RTL_BIDI = frozenset(("R", "AL", "AN"))


def rtl_display(name: str) -> str:
    """Reverse name if its first word consists of RTL characters."""
    first_word = name.split()[0] if name.split() else name
    if all(unicodedata.bidirectional(c) in _RTL_BIDI for c in first_word):
        return name[::-1]
    return name

"""Runtime path helpers that work for both source checkouts and PyInstaller builds."""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def user_data_dir() -> Path:
    return Path.home() / "evernote-to-gdrive"


def cwd_cache_dir() -> Path:
    return Path.cwd() / ".cache"


def repo_root_or_none() -> Path | None:
    if is_frozen():
        return None
    return Path(__file__).resolve().parent.parent.parent

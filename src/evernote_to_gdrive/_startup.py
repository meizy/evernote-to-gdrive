"""Logging setup + startup diagnostics (version / env / subcommand / params)."""
from __future__ import annotations

import datetime
import logging
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import click

from ._console import console

_log = logging.getLogger("evernote_to_gdrive")


def configure_logging(debug: bool) -> None:
    class _ConsoleHandler(logging.Handler):
        def emit(self, record):
            console.print(self.format(record), markup=False, highlight=False)

    pkg_log = logging.getLogger("evernote_to_gdrive")
    pkg_log.setLevel(logging.DEBUG if debug else logging.INFO)
    pkg_log.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fmt.formatTime = lambda record, datefmt=None: (  # type: ignore[method-assign]
        datetime.datetime.fromtimestamp(record.created).strftime("%H:%M:%S.") +
        f"{int(record.msecs):03d}"
    )
    if not any(isinstance(h, _ConsoleHandler) for h in pkg_log.handlers):
        console_handler = _ConsoleHandler()
        console_handler.setFormatter(fmt)
        pkg_log.addHandler(console_handler)
    if debug and not any(isinstance(h, logging.FileHandler) for h in pkg_log.handlers):
        file_handler = logging.FileHandler(Path.cwd() / "debug.log", mode="w", encoding="utf-8")
        file_handler.setFormatter(fmt)
        pkg_log.addHandler(file_handler)


def log_startup(ctx: click.Context) -> None:
    if not _log.isEnabledFor(logging.DEBUG):
        return
    try:
        ver = version("evernote-to-gdrive")
    except PackageNotFoundError:
        ver = "unknown"
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    os_line = f"{platform.system()} {platform.release()} {platform.machine()}"
    _log.debug(
        "evernote-to-gdrive %s | Python %s | %s | cwd: %s",
        ver, py, os_line, Path.cwd(),
    )
    parts = [f"{k}={_fmt(v)}" for k, v in ctx.params.items()]
    _log.debug("%s: %s", ctx.info_name, " ".join(parts))


def _fmt(v: object) -> str:
    if isinstance(v, Path):
        return repr(str(v))
    if isinstance(v, tuple):
        return "[" + ",".join(repr(str(x)) for x in v) + "]"
    if isinstance(v, str):
        return repr(v)
    return str(v)

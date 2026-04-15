"""
Retry logic, write-throttle, and session byte-counter for Google Drive API calls.
"""

from __future__ import annotations

import logging
import time

from googleapiclient.errors import HttpError

from .display import rtl_display

_log = logging.getLogger(__name__)

# Max retries for rate-limit / transient errors
_MAX_RETRIES = 5
_RETRY_STATUS = {429, 500, 502, 503, 504}

# Write throttle: sustained limit is 3 writes/sec per Google Drive API docs
_WRITE_INTERVAL = 0.34  # ~1/3 sec between write calls
_last_write: float = 0.0
_throttle_sleep_total: float = 0.0

# Session byte counter for 750 GB daily upload limit detection
_bytes_uploaded: int = 0


def get_bytes_uploaded() -> int:
    return _bytes_uploaded


def add_bytes_uploaded(n: int) -> None:
    global _bytes_uploaded
    _bytes_uploaded += n


def get_throttle_sleep_total() -> float:
    return _throttle_sleep_total


def reset_throttle_sleep_total() -> None:
    global _throttle_sleep_total
    _throttle_sleep_total = 0.0


def log_throttle_summary(notebook: str, nb_elapsed: float) -> None:
    _log.debug(
        "%s: throttle sleep %.1fs / %.1fs total (%.0f%%)",
        rtl_display(notebook), _throttle_sleep_total, nb_elapsed,
        100 * _throttle_sleep_total / nb_elapsed if nb_elapsed else 0,
    )


def _write_throttle() -> None:
    global _last_write, _throttle_sleep_total
    now = time.monotonic()
    elapsed = now - _last_write
    if elapsed < _WRITE_INTERVAL:
        sleep_for = _WRITE_INTERVAL - elapsed
        time.sleep(sleep_for)
        _throttle_sleep_total += sleep_for
    _last_write = time.monotonic()


def _retry(fn, *args, op: str = "", throttle: bool = False, **kwargs):
    """Call fn(*args, **kwargs) with exponential backoff on transient errors.

    If throttle=True, calls _write_throttle() before each attempt (for write ops).
    """
    delay = 1.0
    for attempt in range(_MAX_RETRIES):
        if throttle:
            _write_throttle()
        try:
            return fn(*args, **kwargs)
        except HttpError as exc:
            if exc.status_code not in _RETRY_STATUS or attempt == _MAX_RETRIES - 1:
                raise
            _log.debug(
                "API error %s — retrying in %.0fs (attempt %d/%d)",
                exc.status_code, delay, attempt + 1, _MAX_RETRIES,
            )
            time.sleep(delay)
            delay *= 2
        except Exception as exc:
            if op:
                raise type(exc)(f"[{op}] {exc}") from exc
            raise
    raise RuntimeError("retry loop exited unexpectedly")


def _write_retry(fn, *args, op: str = "", **kwargs):
    """Like _retry, but throttles to 3 writes/sec before each attempt."""
    return _retry(fn, *args, op=op, throttle=True, **kwargs)

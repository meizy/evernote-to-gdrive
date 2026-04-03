"""Shared image-processing utilities."""

from __future__ import annotations

import io


def apply_exif_orientation(data: bytes, mime: str) -> bytes:
    """Return JPEG bytes with EXIF orientation applied (pixels rotated as needed).

    Falls back to the original bytes on any error or if the image has no EXIF.
    Only meaningful for JPEG; PNG/GIF/WebP don't carry EXIF orientation in practice.
    """
    if mime != "image/jpeg":
        return data
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(data))
        rotated = ImageOps.exif_transpose(img)
        if rotated is img:          # no-op: no orientation tag present
            return data
        buf = io.BytesIO()
        rotated.save(buf, format="JPEG")
        return buf.getvalue()
    except Exception:
        return data

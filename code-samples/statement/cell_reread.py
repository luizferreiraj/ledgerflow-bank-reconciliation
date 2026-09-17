"""Reread ONE cell of the PDF, enlarged and with a restricted alphabet (see ADR 3).

Whole-page recognition gets a few cells wrong and the rest right, and the fault is in the task,
not in the engine: `--psm 6` treats the sheet as one block of running text, across 41 pages of
table, light-grey font on the debits and scanning noise. When the question shrinks to "what
number is in THIS rectangle", the same tesseract gets it right.

## Why only now, and not always

Rereading everything this way would cost one 600 dpi render and three passes per cell, times
more than a thousand lines. It is also unnecessary: the page reading gets the vast majority
right. This runs after a refusal, and ONLY on the cells the arithmetic pointed at: readings that
didn't become a number, and entries from days that don't close.

The arithmetic says where to look; the reread reads; the arithmetic checks what it read. Neither
of the two decides alone.

## The three modes, and why one is not enough

Measured on `sicoob-ib-2026-05-escaneado.pdf`::

    cell              printed      psm 7        psm 8 / 13
    pg14 amount       614,52D      5614,52D     614,52D      <- 8 is right
    pg37 amount       238,34D      238,34D      538,34       <- 7 is right
    pg40 amount       372,46C      5372,46C     372,46C      <- 8 is right
    pg10 unreadable   236,50C      R$ 236,50C   R$ 236,500   <- 7 is right

No mode gets everything right, and every cell is read right by at least one. So all three run
and return CANDIDATES; the choice is not made here. The arithmetic chooses: the candidate that
makes the day close gets in, and if none does, none gets in and the question stays with the
person.
"""

from __future__ import annotations

import io
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# The alphabet of what can exist in an amount cell. Restricting it is half the gain: without it,
# tesseract considers letters and punctuation that don't belong there, and that is how a `5`
# becomes `S` and a `1` becomes `l`.
ALPHABET = "0123456789.,CD$R "
PSM_MODES = ("7", "8", "13")
DPI = 600
UPSCALE_FACTOR = 3
MARGIN = 5.0


@dataclass(frozen=True, slots=True)
class Box:
    """The cell's rectangle, in PDF points."""

    page: int
    x0: float
    top: float
    x1: float
    bottom: float


@lru_cache(maxsize=8)
def _rendered_page(pathname: str, page: int, dpi: int):
    """A page at high resolution. Cached because several cells from the same day fall on the
    same sheet, and rendering at 600 dpi is the expensive step."""
    import pypdfium2

    document = pypdfium2.PdfDocument(pathname)
    try:
        return document[page - 1].render(scale=dpi / 72).to_pil()
    finally:
        document.close()


def candidates(pathname: Path, box: Box, dpi: int = DPI) -> list[str]:
    """What each mode read in that rectangle, without duplicates and without choosing.

    Returns an empty list when OCR is not available or the page won't open: the reread is an
    extra resource, and its absence must never break the reading that already exists.
    """
    from . import ocr

    if not ocr.available():
        return []
    try:
        image = _rendered_page(str(pathname), box.page, dpi)
    except Exception:  # pragma: no cover - an unreadable PDF was already handled earlier
        return []

    zoom = dpi / 72
    cropped = image.crop(
        (
            max(0, int((box.x0 - MARGIN) * zoom)),
            max(0, int((box.top - MARGIN) * zoom)),
            int((box.x1 + MARGIN) * zoom),
            int((box.bottom + MARGIN) * zoom),
        )
    )
    if not cropped.width or not cropped.height:
        return []
    # Upscale after cropping instead of rendering the whole page larger: tesseract works better
    # with large glyphs, and the cost stays within the rectangle.
    cropped = cropped.resize((cropped.width * UPSCALE_FACTOR, cropped.height * UPSCALE_FACTOR))
    buffer = io.BytesIO()
    cropped.save(buffer, format="PNG")
    png = buffer.getvalue()

    seen: list[str] = []
    for psm_mode in PSM_MODES:
        as_read = _tesseract(png, psm_mode)
        if as_read and as_read not in seen:
            seen.append(as_read)
    return seen


def _tesseract(png: bytes, psm_mode: str) -> str:
    try:
        completed = subprocess.run(
            [
                "tesseract", "stdin", "stdout",
                "--psm", psm_mode,
                "-c", f"tessedit_char_whitelist={ALPHABET}",
            ],
            input=png,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.decode("utf-8", "replace").strip().replace("\n", " ")


def clear_cache() -> None:
    """Release the rendered pages. Each one takes tens of MB at 600 dpi, and the firm's machine
    has 3.7 GB."""
    _rendered_page.cache_clear()

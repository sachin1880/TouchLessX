"""Handwriting -> text for the Holo writing pad, using the text recognition built into
macOS (the Vision framework). It runs on this Mac: no network, nothing leaves it.

Strokes are lists of (x, y) points in pad pixels. They are drawn dark-on-light, thick and
with a margin - what the recognizer reads best - and handed over as an in-memory image.
Without macOS Vision (another OS, or pyobjc-framework-Vision missing) `available()` is
False and the pad still draws; it just can't convert.
"""
from __future__ import annotations

import io
import logging

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

INK_WIDTH = 11       # in the recognizer's image: about a tenth of the letter height, like a pen
MARGIN = 40
TEXT_HEIGHT = 110    # the ink is resized to this tall before reading
MAX_WIDTH = 3000

try:  # optional: macOS only
    import Vision
    from Foundation import NSData
except ImportError:  # pragma: no cover - depends on the OS
    Vision = None


def available() -> bool:
    return Vision is not None


def strokes_image(strokes: list[list[tuple[float, float]]], scale: float | None = None) -> Image.Image | None:
    """The strokes as a black-on-white image cropped to the ink, or None if there's none.

    Without `scale` the ink is resized to about TEXT_HEIGHT pixels tall: air-written letters
    can be hundreds of pixels high with comparatively thin lines - wire letters the
    recognizer doesn't read (measured: "TO", "CAT" -> nothing at 600 px, read at 110 px)."""
    points = [p for stroke in strokes for p in stroke]
    if not points:
        return None
    left, top = min(p[0] for p in points), min(p[1] for p in points)
    right, bottom = max(p[0] for p in points), max(p[1] for p in points)
    if scale is None:
        scale = TEXT_HEIGHT / max(bottom - top, 1.0)
        scale = min(scale, MAX_WIDTH / max(right - left, 1.0))  # a long line of writing stays a sane size
    width = round((right - left) * scale) + 2 * MARGIN
    height = round((bottom - top) * scale) + 2 * MARGIN
    image = Image.new("L", (max(width, 64), max(height, 64)), 255)
    draw = ImageDraw.Draw(image)
    for stroke in strokes:
        line = [((x - left) * scale + MARGIN, (y - top) * scale + MARGIN) for x, y in stroke]
        if len(line) == 1:
            x, y = line[0]
            r = INK_WIDTH / 2
            draw.ellipse((x - r, y - r, x + r, y + r), fill=0)
        else:
            draw.line(line, fill=0, width=INK_WIDTH, joint="curve")
            for x, y in (line[0], line[-1]):  # round the ends
                r = INK_WIDTH / 2
                draw.ellipse((x - r, y - r, x + r, y + r), fill=0)
    return image


def recognize(image: Image.Image, languages: tuple[str, ...] = ("en-US",)) -> str:
    """The text in the image, lines joined by spaces; "" when nothing was read. Blocking
    (a few hundred ms): call it off the UI thread."""
    if Vision is None:
        raise RuntimeError("Handwriting recognition needs macOS and pyobjc-framework-Vision")
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    raw = buffer.getvalue()
    data = NSData.dataWithBytes_length_(raw, len(raw))
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)  # the one that reads handwriting
    request.setUsesLanguageCorrection_(True)
    request.setRecognitionLanguages_(list(languages))
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        logger.warning("Handwriting recognition failed: %s", error)
        return ""
    lines = []
    for observation in sorted(request.results() or [], key=lambda o: -o.boundingBox().origin.y):
        candidates = observation.topCandidates_(1)
        if candidates:
            lines.append(str(candidates[0].string()))
    return " ".join(line.strip() for line in lines if line.strip())

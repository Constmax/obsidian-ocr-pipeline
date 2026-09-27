"""Fast mode: Apple Vision lines, citation lines re-read by PP-OCRv5.

Apple Vision (VNRecognizeTextRequest) finds and reads a page 3-7 times faster
than RapidOCR on an M1, but reads citations worse ("$ 935" for "§ 935",
"§ 568 | BGB" for "§ 568 I BGB"). Fast mode therefore takes Vision's lines
and boxes, and only the lines that look like a citation are cropped along
the line and read again by RapidOCR's recognizer, without its detector
(bench/ERGEBNIS.md, Nachtrag 22). A re-reading replaces Vision's text only
when RapidOCR is sure of it and it stays close to Vision's. Reading order
still comes from ordering.order_lines().

PyObjC's Vision bindings are imported only when a page is recognized or the
runtime is checked, so the rest of this module is testable anywhere.
"""

from __future__ import annotations

import difflib
import logging
import math
import platform
import re
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ocrmypdf_paddle import runtime
from ocrmypdf_paddle.hocr import Point, TextLine, Word

log = logging.getLogger(__name__)

#: Vision's language for OCRmyPDF's -l deu (runtime.LANGUAGES has the only key).
VISION_LANGUAGE = "de-DE"

#: VNRecognizeTextRequest.automaticallyDetectsLanguage, which vision_lines()
#: turns off, exists from macOS 13 on.
MIN_MACOS = (13, 0)

#: A line worth re-reading: a section or article sign, a dollar sign Vision
#: reads for "§", or a number followed by a Roman numeral that Vision may read
#: as "|", "l" or "1" ("§ 568 | BGB", "§ 58 Il VwGO"). The numeral ends where
#: no word character follows; `\b` would not match after "|".
CITATION_HINT = re.compile(r"[§$]|\bArt\.|\d\s+[IVXl|1]{1,4}(?!\w)")

#: Margin around a line before re-reading, as a share of the line height,
#: added along the line's own edges. RapidOCR's recognizer expects a little
#: background around the glyphs.
CROP_MARGIN = 0.15

#: A re-reading replaces Vision's line only with at least this recognition
#: score and this difflib similarity to Vision's text. A citation fix changes
#: a few characters ("$ 935 BGB" -> "§ 935 BGB" is 0.89); a reading of noise
#: or of a neighbouring line differs in most. Not calibrated yet (#71).
REREAD_MIN_SCORE = 0.8
REREAD_MIN_SIMILARITY = 0.6


def needs_rereading(text: str) -> bool:
    return bool(CITATION_HINT.search(text))


def _macos_version() -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in platform.mac_ver()[0].split("."))
    except ValueError:
        return ()


def missing_vision() -> list[str]:
    """Problems with Apple Vision for fast mode; empty when it can read German."""
    if sys.platform != "darwin":
        return ["fast mode needs Apple Vision, which exists only on macOS"]
    version = _macos_version()
    if version < MIN_MACOS:
        found = ".".join(map(str, version)) or "an unknown version"
        return [f"fast mode needs macOS {'.'.join(map(str, MIN_MACOS))} or later "
                f"for Apple Vision, not {found}"]
    try:
        import objc
        import Vision
    except ImportError:
        return ["pyobjc-framework-Vision is not installed (pip install 'ocrmypdf-paddle[fast]')"]
    with objc.autorelease_pool():
        request = Vision.VNRecognizeTextRequest.alloc().init()
        if not hasattr(request, "setAutomaticallyDetectsLanguage_"):
            return ["Apple Vision on this macOS cannot turn off language detection"]
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        languages, _ = request.supportedRecognitionLanguagesAndReturnError_(None)
    if VISION_LANGUAGE not in (languages or []):
        return [f"Apple Vision on this macOS cannot read {VISION_LANGUAGE}"]
    return []


def pixel_polygon(points: Sequence[tuple[float, float]], width: int,
                  height: int) -> tuple[Point, ...]:
    """Vision's normalized, bottom-left-origin points in image pixels."""
    return tuple((x * width, (1.0 - y) * height) for x, y in points)


def _corners(observation: Any) -> list[tuple[float, float]]:
    """Top-left, top-right, bottom-right, bottom-left, as RapidOCR orders boxes."""
    return [(p.x, p.y) for p in (observation.topLeft(), observation.topRight(),
                                 observation.bottomRight(), observation.bottomLeft())]


def _utf16_offset(text: str, index: int) -> int:
    """Vision ranges count UTF-16 code units, Python strings code points."""
    return len(text[:index].encode("utf-16-le")) // 2


def _words(candidate: Any, text: str, width: int, height: int) -> tuple[Word, ...]:
    """One Word per whitespace token, or none if Vision cannot box every token.

    Vision's word boxes reach into half the space on either side, so
    neighbouring words touch; hocr.render_page() trims them like any
    recognizer's word boxes.
    """
    words = []
    for match in re.finditer(r"\S+", text):
        start = _utf16_offset(text, match.start())
        length = _utf16_offset(text, match.end()) - start
        box, _ = candidate.boundingBoxForRange_error_((start, length), None)
        if box is None:
            return ()
        polygon = pixel_polygon(_corners(box), width, height)
        words.append(Word(match.group(), polygon, float(candidate.confidence())))
    return tuple(words)


def vision_lines(image_path: Path, width: int, height: int) -> list[TextLine]:
    """Apple Vision's lines with polygons and word boxes, in Vision's order."""
    import Foundation
    import objc
    import Vision

    with objc.autorelease_pool():
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setAutomaticallyDetectsLanguage_(False)
        request.setRecognitionLanguages_([VISION_LANGUAGE])
        request.setUsesLanguageCorrection_(True)
        url = Foundation.NSURL.fileURLWithPath_(str(Path(image_path).absolute()))
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(f"Apple Vision could not read {Path(image_path).name}: {error}")
        lines = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            text = str(candidate.string())
            polygon = pixel_polygon(_corners(observation), width, height)
            lines.append(TextLine(
                text=text,
                polygon=polygon,
                confidence=float(candidate.confidence()),
                words=_words(candidate, text, width, height),
            ))
        return lines


def _unit(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    return (dx / length, dy / length) if length > 0 else (0.0, 0.0)


def crop_quad(polygon: Sequence[Point], width: int,
              height: int) -> tuple[tuple[Point, ...], tuple[int, int]] | None:
    """The line quadrilateral widened by CROP_MARGIN along its own edges, and
    the size of the upright crop it maps to.

    None for anything but four points, a line under one pixel long or high,
    or a line wholly outside the image. Following the line's edges instead of
    its axis-aligned box keeps the neighbouring lines out of a skewed line's
    crop, as RapidOCR's own perspective crop does.
    """
    if len(polygon) != 4:
        return None
    tl, tr, br, bl = polygon
    length = (math.dist(tl, tr) + math.dist(bl, br)) / 2
    line_height = (math.dist(tl, bl) + math.dist(tr, br)) / 2
    xs = [x for x, _ in polygon]
    ys = [y for _, y in polygon]
    if (length < 1 or line_height < 1 or max(xs) <= 0 or max(ys) <= 0
            or min(xs) >= width or min(ys) >= height):
        return None
    margin = CROP_MARGIN * line_height
    ux, uy = _unit(tr[0] - tl[0] + br[0] - bl[0], tr[1] - tl[1] + br[1] - bl[1])
    vx, vy = _unit(bl[0] - tl[0] + br[0] - tr[0], bl[1] - tl[1] + br[1] - tr[1])

    def shifted(point: Point, along: int, across: int) -> Point:
        return (point[0] + margin * (along * ux + across * vx),
                point[1] + margin * (along * uy + across * vy))

    quad = (shifted(tl, -1, -1), shifted(tr, 1, -1), shifted(br, 1, 1), shifted(bl, -1, 1))
    return quad, (max(1, round(length + 2 * margin)), max(1, round(line_height + 2 * margin)))


def crop_line(image: Any, polygon: Sequence[Point]) -> Any:
    """An upright RGB image of one line with CROP_MARGIN, or None (crop_quad).

    Only the region under the line is read and converted, never the whole
    page; the margin beyond the image edge is white.
    """
    from PIL import Image

    width, height = image.size
    geometry = crop_quad(polygon, width, height)
    if geometry is None:
        return None
    quad, size = geometry
    left = max(0, math.floor(min(x for x, _ in quad)))
    top = max(0, math.floor(min(y for _, y in quad)))
    right = min(width, math.ceil(max(x for x, _ in quad)))
    bottom = min(height, math.ceil(max(y for _, y in quad)))
    region = image.crop((left, top, right, bottom)).convert("RGB")
    # PIL's QUAD takes the source corners upper left, lower left, lower right,
    # upper right.
    tl, tr, br, bl = quad
    data = [c for x, y in (tl, bl, br, tr) for c in (x - left, y - top)]
    return region.transform(size, Image.Transform.QUAD, data, Image.Resampling.BICUBIC,
                            fillcolor=(255, 255, 255))


def accepts_rereading(vision_text: str, text: str, score: float) -> bool:
    """Whether RapidOCR's re-reading of a line should replace Vision's."""
    if not text or text == vision_text or not score >= REREAD_MIN_SCORE:
        return False
    return difflib.SequenceMatcher(None, vision_text, text).ratio() >= REREAD_MIN_SIMILARITY


class FastRecognizer:
    """Apple Vision lines; citation lines re-read by RapidOCR. One call at a time.

    `reader` is the accurate mode's runtime.Recognizer, so both modes share
    one RapidOCR pipeline, created only when the first line needs re-reading.
    `vision` is replaceable for tests.
    """

    def __init__(
        self,
        reader: runtime.Recognizer | None = None,
        vision: Callable[[Path, int, int], list[TextLine]] | None = None,
    ) -> None:
        self._reader = reader or runtime.Recognizer()
        self._vision = vision or vision_lines
        self._lock = threading.Lock()

    def recognize(self, image_path: Path) -> runtime.RecognizedPage:
        from PIL import Image

        with self._lock:
            with Image.open(image_path) as image:
                width, height = image.size
                lines = self._vision(Path(image_path), width, height)
                picked = [i for i, line in enumerate(lines) if needs_rereading(line.text)]
                replaced = 0
                for i in picked:
                    line = self._reread(image, lines[i])
                    replaced += line is not lines[i]
                    lines[i] = line
            log.debug("%s: %d Vision lines, %d re-read, %d replaced", Path(image_path).name,
                      len(lines), len(picked), replaced)
            return runtime.RecognizedPage(width, height, lines)

    def _reread(self, image: Any, line: TextLine) -> TextLine:
        crop = crop_line(image, line.polygon)
        if crop is None:
            return line
        text, score = self._reader.recognize_line(crop)
        if not accepts_rereading(line.text, text, score):
            return line
        # Vision's word boxes fit the re-read words only word for word ("$ 935"
        # → "§ 935"); otherwise the hOCR spreads the words across the line box.
        # The score is RapidOCR's, since the text is.
        tokens = text.split()
        words = ()
        if len(tokens) == len(line.words):
            words = tuple(Word(token, word.polygon, score)
                          for token, word in zip(tokens, line.words))
        return TextLine(text, line.polygon, score, words)

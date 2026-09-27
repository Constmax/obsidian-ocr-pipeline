"""Fast mode: Apple Vision lines, citation lines re-read by PP-OCRv5.

Apple Vision (VNRecognizeTextRequest) finds and reads a page 3-7 times faster
than RapidOCR on an M1, but reads citations worse ("$ 935" for "§ 935",
"§ 568 | BGB" for "§ 568 I BGB"). Fast mode therefore takes Vision's lines
and boxes, and only the lines that look like a citation are cropped and read
again by RapidOCR's recognizer, without its detector (bench/ERGEBNIS.md,
Nachtrag 22). Reading order still comes from ordering.order_lines().

PyObjC's Vision bindings are imported only when a page is recognized or the
runtime is checked, so the rest of this module is testable anywhere.
"""

from __future__ import annotations

import logging
import math
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

#: A line worth re-reading: a section or article sign, a dollar sign Vision
#: reads for "§", or a number followed by a Roman numeral that Vision may read
#: as "|", "l" or "1" ("§ 568 | BGB", "§ 58 Il VwGO").
CITATION_HINT = re.compile(r"[§$]|\bArt\.|\d\s+[IVXl|1]{1,4}\b")

#: Margin around a line box before re-reading, as a share of the box height.
#: RapidOCR's recognizer expects a little background around the glyphs.
CROP_MARGIN = 0.15

#: Vision's word boxes reach into half of the space on either side, so
#: neighbouring words touch. Rendered as they are, pdftotext glues the words
#: of a line together (0.3-2 % of the words survived on four truth pages).
#: Each box is trimmed at both ends by this share of the line height, but by
#: at most WORD_TRIM_MAX of its own width; from 0.25 on all words survived.
WORD_TRIM = 0.25
WORD_TRIM_MAX = 0.3


def needs_rereading(text: str) -> bool:
    return bool(CITATION_HINT.search(text))


def missing_vision() -> list[str]:
    """Problems with Apple Vision for fast mode; empty when it can read German."""
    if sys.platform != "darwin":
        return ["fast mode needs Apple Vision, which exists only on macOS"]
    try:
        import objc
        import Vision
    except ImportError:
        return ["pyobjc-framework-Vision is not installed (pip install 'ocrmypdf-paddle[fast]')"]
    with objc.autorelease_pool():
        request = Vision.VNRecognizeTextRequest.alloc().init()
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


def trim_word(polygon: Sequence[Point], line_height: float) -> tuple[Point, ...]:
    """A word quadrilateral (tl, tr, br, bl) shortened at both ends along its edges."""
    tl, tr, br, bl = polygon
    length = math.dist(tl, tr)
    if length <= 0:
        return tuple(polygon)
    t = min(WORD_TRIM * line_height, WORD_TRIM_MAX * length) / length

    def along(a: Point, b: Point, share: float) -> Point:
        return (a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share)

    return (along(tl, tr, t), along(tl, tr, 1 - t), along(bl, br, 1 - t), along(bl, br, t))


def _words(candidate: Any, text: str, line: Sequence[Point], width: int,
           height: int) -> tuple[Word, ...]:
    """One Word per whitespace token, or none if Vision cannot box every token."""
    line_height = math.dist(line[0], line[3])
    words = []
    for match in re.finditer(r"\S+", text):
        start = _utf16_offset(text, match.start())
        length = _utf16_offset(text, match.end()) - start
        box, _ = candidate.boundingBoxForRange_error_((start, length), None)
        if box is None:
            return ()
        polygon = trim_word(pixel_polygon(_corners(box), width, height), line_height)
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
                words=_words(candidate, text, polygon, width, height),
            ))
        return lines


def crop_box(polygon: Sequence[Point], width: int, height: int) -> tuple[int, int, int, int] | None:
    """Axis-aligned crop around a line with CROP_MARGIN, clamped to the image."""
    xs = [x for x, _ in polygon]
    ys = [y for _, y in polygon]
    if not xs:
        return None
    margin = CROP_MARGIN * (max(ys) - min(ys))
    box = (max(0, round(min(xs) - margin)), max(0, round(min(ys) - margin)),
           min(width, round(max(xs) + margin)), min(height, round(max(ys) + margin)))
    if box[2] - box[0] < 1 or box[3] - box[1] < 1:
        return None
    return box


def reread_text(engine: Any, crop: Any) -> str:
    """RapidOCR's recognizer alone on one line image; "" when it reads nothing."""
    result = engine(crop, use_det=False, use_cls=False, use_rec=True)
    texts = [text for text in (getattr(result, "txts", None) or ()) if isinstance(text, str)]
    return " ".join(texts).strip()


class FastRecognizer:
    """Apple Vision lines; citation lines re-read by RapidOCR. One call at a time.

    `vision` and `reader_factory` are replaceable for tests. RapidOCR is
    created only when the first line needs re-reading.
    """

    def __init__(
        self,
        vision: Callable[[Path, int, int], list[TextLine]] | None = None,
        reader_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._vision = vision or vision_lines
        self._reader_factory = reader_factory or runtime.create_rapidocr
        self._lock = threading.Lock()
        self._reader: Any = None

    def recognize(self, image_path: Path) -> runtime.RecognizedPage:
        from PIL import Image

        with self._lock:
            with Image.open(image_path) as image:
                width, height = image.size
                lines = self._vision(Path(image_path), width, height)
                picked = [i for i, line in enumerate(lines) if needs_rereading(line.text)]
                if picked:
                    rgb = image.convert("RGB")
                    for i in picked:
                        lines[i] = self._reread(rgb, lines[i], width, height)
            log.debug("%s: %d Vision lines, %d re-read", Path(image_path).name,
                      len(lines), len(picked))
            return runtime.RecognizedPage(width, height, lines)

    def _reread(self, image: Any, line: TextLine, width: int, height: int) -> TextLine:
        box = crop_box(line.polygon, width, height)
        if box is None:
            return line
        if self._reader is None:
            self._reader = self._reader_factory()
        text = reread_text(self._reader, image.crop(box))
        if not text or text == line.text:
            return line
        # Vision's word boxes fit the re-read words only word for word ("$ 935"
        # → "§ 935"); otherwise the hOCR spreads the words across the line box.
        tokens = text.split()
        words = ()
        if len(tokens) == len(line.words):
            words = tuple(Word(token, word.polygon, word.confidence)
                          for token, word in zip(tokens, line.words))
        return TextLine(text, line.polygon, line.confidence, words)

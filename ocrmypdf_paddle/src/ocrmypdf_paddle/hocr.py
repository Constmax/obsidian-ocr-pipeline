"""Polygon-to-hOCR conversion for the PaddleOCR engine.

Pure module: no OCRmyPDF, RapidOCR or numpy imports.

OCRmyPDF 17.8.0 writes the text layer in hOCR element order
(bin/test/test_hocr_text_layer_order.py), so the hOCR and the plain-text
sidecar are both generated here from one ordered line sequence.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from html import escape

Point = tuple[float, float]
Box = tuple[int, int, int, int]

#: Every recognized line is written as one hOCR word that holds the whole
#: line. OCRmyPDF's renderer stretches each hOCR word to its own box (Tz), and
#: pdf.js 4.10 (Obsidian) then judges the room between two words from the end
#: of the first word's last letter: above 0.6 font sizes
#: (SPACE_IN_FLOW_MAX_FACTOR) it starts a new text item, and each item is its
#: own highlight block with a hole before the next. Word boxes left that much
#: room after every word stretched beyond about 1.35 ("BGB", "VwGO"). In a
#: rotated line pdf.js also compares heights at a scale that includes each
#: word's stretch (applyInverseRotation), so every word became its own block.
#: One word per line has one stretch: the line is one text item, flat or
#: rotated, and its spaces are the font's own, which pdftotext reads as spaces.

#: Recognizers split a printed line into lines that touch or overlap on one
#: baseline. A line therefore starts and ends this share of the font size
#: inside its box, so that two such lines keep a space between them.
WORD_ROOM = 0.25

#: ...but by at most this share of the line's width at either end, so a short
#: line ("I", "§") keeps most of its box.
EDGE_ROOM_MAX = 0.3

#: OCRmyPDF 17.8.0 renders a line whose baseline slope is below this flat
#: (fpdf_renderer, _render_line) and turns a steeper one about the baseline at
#: the left edge of its box.
FLAT_SLOPE = 0.005

#: Where the baseline lies in a recognizer's line box, as a share of the line
#: height from its top edge. Neither recognizer's box ends at the baseline:
#: both reach below the descenders. OCRmyPDF's renderer sizes the font from
#: the box top to the baseline, and viewers highlight a selection over the
#: font's em box around the baseline, so a baseline on the box bottom pushed
#: every highlight half a line down. Measured on five vault pages: Vision's
#: baselines lie at 0.68-0.81 of the line height (median per page), RapidOCR's
#: at 0.75.
BASELINE_SHARE = 0.75

@dataclass(frozen=True)
class Word:
    """A piece of a line as the recognizer boxed it: a word or a character.

    The text layer does not use the pieces (see WORD_ROOM); they stay in the
    debug record.
    """

    text: str
    polygon: tuple[Point, ...]
    confidence: float


@dataclass(frozen=True)
class TextLine:
    """One recognized line: text, quadrilateral, score, and its pieces."""

    text: str
    polygon: tuple[Point, ...]
    confidence: float
    words: tuple[Word, ...] = ()


def wconf(confidence: object) -> int:
    """Recognition score in [0, 1] as hOCR's integer x_wconf in 0-100.

    There is no cutoff: every line is kept until the benchmark (#71)
    calibrates one. Out-of-range scores are clamped; a missing or non-numeric
    score becomes 0.
    """
    try:
        value = float(confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(value):
        return 0
    return round(min(max(value, 0.0), 1.0) * 100)


def clamp_polygon(points: object, width: int, height: int) -> tuple[Point, ...] | None:
    """A recognizer quadrilateral clamped to the image, or None if unusable.

    Unusable means: not exactly four (x, y) points, a coordinate that is not a
    finite number, or less than one pixel of width, height, or spread once
    clamped. A degenerate or collinear box cannot carry text.
    """
    try:
        raw = [(float(x), float(y)) for x, y in points]  # type: ignore[attr-defined]
    except (TypeError, ValueError):
        return None
    if len(raw) != 4 or not all(math.isfinite(c) for point in raw for c in point):
        return None
    clamped = tuple(
        (min(max(x, 0.0), float(width)), min(max(y, 0.0), float(height))) for x, y in raw
    )
    xs = [x for x, _ in clamped]
    ys = [y for _, y in clamped]
    if max(xs) - min(xs) < 1 or max(ys) - min(ys) < 1 or _spread(clamped) < 1:
        return None
    return clamped


def _spread(polygon: Sequence[Point]) -> float:
    """Largest triangle area among the points; 0 when all are collinear.

    Unlike the polygon area this does not depend on the point order.
    """
    best = 0.0
    for (ax, ay), (bx, by), (cx, cy) in itertools.combinations(polygon, 3):
        best = max(best, abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) / 2)
    return best


def bounding_box(polygon: Sequence[Point]) -> Box:
    """Smallest integer box that contains the polygon."""
    xs = [x for x, _ in polygon]
    ys = [y for _, y in polygon]
    return (math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs)), math.ceil(max(ys)))


class LineGeometry:
    """Top edge, baseline and bottom edge of a line quadrilateral along x.

    The baseline runs BASELINE_SHARE of the way from the top edge to the
    bottom edge.
    """

    def __init__(self, polygon: Sequence[Point]) -> None:
        by_x = sorted(polygon)
        (self._tl, self._bl) = sorted(by_x[:2], key=lambda point: point[1])
        (self._tr, self._br) = sorted(by_x[2:], key=lambda point: point[1])
        # A sliver clipped at the page edge can pair its corners so that the
        # edges cross; its bounding box then stands in (at least 1 px high).
        left, right = by_x[0][0], by_x[-1][0]
        if min(self.bottom(left) - self.top(left), self.bottom(right) - self.top(right)) < 1:
            x0, y0, x1, y1 = bounding_box(polygon)
            self._tl, self._bl, self._tr, self._br = (x0, y0), (x0, y1), (x1, y0), (x1, y1)

    @staticmethod
    def _at(start: Point, end: Point, x: float) -> float:
        dx = end[0] - start[0]
        return start[1] + ((end[1] - start[1]) * (x - start[0]) / dx if dx > 0 else 0.0)

    def top(self, x: float) -> float:
        return self._at(self._tl, self._tr, x)

    def bottom(self, x: float) -> float:
        return self._at(self._bl, self._br, x)

    def baseline(self, x: float) -> float:
        return self.top(x) + BASELINE_SHARE * (self.bottom(x) - self.top(x))

    @property
    def slope(self) -> float:
        return self.baseline(1.0) - self.baseline(0.0)

    @property
    def font_size(self) -> float:
        """Top edge to baseline, the font size OCRmyPDF's renderer derives."""
        middle = (self._tl[0] + self._tr[0]) / 2
        return self.baseline(middle) - self.top(middle)


def render_page(
    lines: Iterable[TextLine], width: int, height: int, language: str = "deu"
) -> tuple[str, str]:
    """hOCR document and sidecar text for `lines` in the order given.

    A line without text or with an unusable polygon is left out of both, so
    the text layer and the sidecar always hold the same lines in the same
    order.
    """
    blocks: list[str] = []
    texts: list[str] = []
    for line in lines:
        tokens = line.text.split() if isinstance(line.text, str) else []
        polygon = clamp_polygon(line.polygon, width, height)
        if not tokens or polygon is None:
            continue
        n = len(texts)
        box = bounding_box(polygon)
        blocks.append(
            f'<div class="ocr_carea" id="block_{n}" title="{_bbox(box)}">\n'
            f'<p class="ocr_par" id="par_{n}" lang="{escape(language)}" title="{_bbox(box)}">\n'
            f"{_line(n, ' '.join(tokens), box, LineGeometry(polygon), wconf(line.confidence))}"
            "\n</p>\n</div>"
        )
        texts.append(" ".join(tokens))
    hocr = _TEMPLATE.format(width=width, height=height, content="\n".join(blocks))
    return hocr, ("\n".join(texts) + "\n") if texts else ""


def _line(n: int, text: str, box: Box, geometry: LineGeometry, confidence: int) -> str:
    """One ocr_line holding the whole line as one word.

    OCRmyPDF's renderer reads the font size from the box top to the baseline
    and places the baseline `intercept` above the box bottom at the box's left
    edge, so the box takes the line's top and bottom edge at one point: the
    left end of a sloped line, the middle of one rendered flat.
    """
    x0, _, x1, _ = box
    inset = min(WORD_ROOM * geometry.font_size, EDGE_ROOM_MAX * (x1 - x0))
    left = min(math.ceil(x0 + inset), x1 - 1)
    right = max(left + 1, math.floor(x1 - inset))
    sloped = abs(geometry.slope) >= FLAT_SLOPE
    at = left if sloped else (left + right) / 2
    top, bottom = math.floor(geometry.top(at)), math.ceil(geometry.bottom(at))
    intercept = min(0, round(geometry.baseline(at) - bottom))
    title = _bbox((left, top, right, bottom))
    # Fixed notation: the parser drops a baseline whose slope has an exponent.
    slope = f"{geometry.slope:.4f}" if sloped else "0"
    return (
        f'<span class="ocr_line" id="line_{n}" title="{title}; baseline {slope} {intercept}">\n'
        f'<span class="ocrx_word" id="word_{n}" title="{title}; x_wconf {confidence}">'
        f"{escape(text)}</span>\n</span>"
    )


def _bbox(box: Box) -> str:
    return f"bbox {box[0]} {box[1]} {box[2]} {box[3]}"


_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"
    "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
<head>
<title></title>
<meta http-equiv="Content-Type" content="text/html;charset=utf-8"/>
<meta name="ocr-system" content="ocrmypdf-paddle"/>
<meta name="ocr-capabilities" content="ocr_page ocr_carea ocr_par ocr_line ocrx_word"/>
</head>
<body>
<div class="ocr_page" id="page_1" title="bbox 0 0 {width} {height}">
{content}
</div>
</body>
</html>
"""

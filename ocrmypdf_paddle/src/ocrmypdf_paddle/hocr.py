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


@dataclass(frozen=True)
class Word:
    """A piece of a line as the recognizer boxed it: a word or a character."""

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


def baseline(polygon: Sequence[Point], box: Box) -> tuple[float, int]:
    """hOCR baseline of a line quadrilateral as (slope, intercept).

    The baseline runs along the bottom edge: the lower of the two leftmost
    points to the lower of the two rightmost points. As the hOCR spec defines
    it, the intercept is the baseline's height at the box's left edge measured
    from the box's bottom, so it is zero or negative. OCRmyPDF's fpdf2
    renderer places the words along this slope, which keeps selection on
    skewed lines.
    """
    by_x = sorted(polygon)
    left = max(by_x[:2], key=lambda point: point[1])
    right = max(by_x[2:], key=lambda point: point[1])
    dx = right[0] - left[0]
    slope = (right[1] - left[1]) / dx if dx > 0 else 0.0
    at_left = left[1] + slope * (box[0] - left[0])
    return slope, min(0, round(at_left - box[3]))


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
        slope, intercept = baseline(polygon, box)
        words = "\n".join(
            f'<span class="ocrx_word" id="word_{n}_{i}" title="{_bbox(word_box)}; '
            f'x_wconf {confidence}">{escape(text)}</span>'
            for i, (text, word_box, confidence) in enumerate(
                _placed_words(line, tokens, box, width, height)
            )
        )
        blocks.append(
            f'<div class="ocr_carea" id="block_{n}" title="{_bbox(box)}">\n'
            f'<p class="ocr_par" id="par_{n}" lang="{escape(language)}" title="{_bbox(box)}">\n'
            # Fixed-point slope: OCRmyPDF's parser does not read exponent notation.
            f'<span class="ocr_line" id="line_{n}" title="{_bbox(box)}; '
            f'baseline {slope:.6f} {intercept}">\n{words}\n</span>\n</p>\n</div>'
        )
        texts.append(" ".join(tokens))
    hocr = _TEMPLATE.format(width=width, height=height, content="\n".join(blocks))
    return hocr, ("\n".join(texts) + "\n") if texts else ""


def _bbox(box: Box) -> str:
    return f"bbox {box[0]} {box[1]} {box[2]} {box[3]}"


def _placed_words(
    line: TextLine, tokens: list[str], box: Box, width: int, height: int
) -> list[tuple[str, Box, int]]:
    """The line's words with their boxes and x_wconf.

    Uses the recognizer's pieces when they spell the words exactly, merging
    character pieces per word. Otherwise the words are spread across the line
    box by character count; the spike (#62) showed that spread words glue
    together on skewed lines, so this is only the fallback for results
    without usable pieces.
    """
    grouped = _group_pieces(line.words, tokens, width, height)
    if grouped is not None:
        return grouped
    return _spread_evenly(tokens, box, wconf(line.confidence))


def _group_pieces(
    pieces: Sequence[Word], tokens: list[str], width: int, height: int
) -> list[tuple[str, Box, int]] | None:
    queue = [piece for piece in pieces if isinstance(piece.text, str) and piece.text.strip()]
    if not queue:
        return None
    placed = []
    for token in tokens:
        text, boxes, confidences = "", [], []
        while len(text) < len(token) and queue:
            piece = queue.pop(0)
            polygon = clamp_polygon(piece.polygon, width, height)
            if polygon is None:
                return None
            text += "".join(piece.text.split())
            boxes.append(bounding_box(polygon))
            confidences.append(wconf(piece.confidence))
        if text != token:
            return None
        union = (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )
        placed.append((token, union, round(sum(confidences) / len(confidences))))
    return None if queue else placed


def _spread_evenly(tokens: list[str], box: Box, confidence: int) -> list[tuple[str, Box, int]]:
    left, top, right, bottom = box
    # One unit per character plus one per gap between words.
    step = (right - left) / (sum(len(token) for token in tokens) + len(tokens) - 1)
    placed, cursor = [], 0
    for token in tokens:
        x0 = math.floor(left + cursor * step)
        x1 = min(right, max(x0 + 1, math.ceil(left + (cursor + len(token)) * step)))
        placed.append((token, (x0, top, x1, bottom), confidence))
        cursor += len(token) + 1
    return placed


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

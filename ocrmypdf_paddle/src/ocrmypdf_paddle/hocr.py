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

#: OCRmyPDF's renderer stretches each word to its box (Tz) and appends a
#: space at the same stretch. Two readers then judge the room left between
#: that space and the next word, in shares of the font size (OCRmyPDF
#: 17.8.0): pdftotext -raw glues the words below about 0.12 (measured on
#: synthetic lines), and pdf.js 4.10 starts a new text item above 0.6
#: (SPACE_IN_FLOW_MAX_FACTOR), which splits a selection highlight into one
#: block per word. Recognizers box a word up to its neighbours (Apple Vision
#: reaches into half the space on either side, and in justified lines into
#: half the widened space), which left no room at all ("Dabeiistder Verein",
#: bench/ERGEBNIS.md). Each word's right edge is therefore placed so that
#: this much room remains after its space.
WORD_ROOM = 0.25

#: The line's first word starts and its last word ends WORD_ROOM inside their
#: boxes, but by at most this share of the word's width, so a short word on
#: its own ("I", "§") keeps most of its box.
EDGE_ROOM_MAX = 0.3

#: Advance widths in em of NotoSans, OCRmyPDF's Latin text-layer font, for
#: the stretch above: the narrow characters (0.22-0.38 em), an average for
#: the others, and the space. Over German prose the mean stays near a flat
#: 0.55; on a one-column page the font's real widths placed the words no
#: better. With the flat mean alone, narrow words ("ist", "fit") stretched
#: further than modeled and their wider space left pdftotext no room.
NARROW = frozenset("fijltIJ!'(),-.:;[]")
NARROW_ADVANCE = 0.3
AVERAGE_ADVANCE = 0.6
SPACE_ADVANCE = 0.26

#: Where the baseline lies in a recognizer's line box, as a share of the line
#: height from its top edge. Neither recognizer's box ends at the baseline:
#: both reach below the descenders. OCRmyPDF's renderer sizes the font from
#: the box top to the baseline, and viewers highlight a selection over the
#: font's em box around the baseline, so a baseline on the box bottom pushed
#: every highlight half a line down. Measured on five vault pages: Vision's
#: baselines lie at 0.68-0.81 of the line height (median per page), RapidOCR's
#: at 0.75.
BASELINE_SHARE = 0.75

#: Every hOCR line is written flat. OCRmyPDF 17.8.0 renders a sloped line
#: (slope 0.005 or more) rotated, and pdf.js 4.10 undoes a rotation with a
#: scale that includes each word's horizontal stretch (applyInverseRotation),
#: so words of different stretch land on different heights and every word of
#: the line becomes its own highlight block. That hit most lines of
#: one-column pages, whose long lines keep a residual skew after deskewing. A
#: sloped line is therefore cut into flat pieces, each short enough that the
#: real baseline drifts at most this share of the font size across it.
FLAT_DRIFT = 0.2


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
        geometry = LineGeometry(polygon)
        placed = _placed_words(line, tokens, box, geometry.font_size, width, height)
        pieces = "\n".join(
            _flat_line(f"{n}_{k}", piece, geometry)
            for k, piece in enumerate(_flat_pieces(placed, geometry))
        )
        blocks.append(
            f'<div class="ocr_carea" id="block_{n}" title="{_bbox(box)}">\n'
            f'<p class="ocr_par" id="par_{n}" lang="{escape(language)}" title="{_bbox(box)}">\n'
            f"{pieces}\n</p>\n</div>"
        )
        texts.append(" ".join(tokens))
    hocr = _TEMPLATE.format(width=width, height=height, content="\n".join(blocks))
    return hocr, ("\n".join(texts) + "\n") if texts else ""


def _flat_pieces(
    placed: list[tuple[str, Box, int]], geometry: LineGeometry
) -> list[list[tuple[str, Box, int]]]:
    """The line's words in runs over which the baseline drifts at most
    FLAT_DRIFT font sizes; a single word always makes a run."""
    reach = FLAT_DRIFT * geometry.font_size / max(abs(geometry.slope), 1e-9)
    pieces: list[list[tuple[str, Box, int]]] = []
    for word in placed:
        if pieces and word[1][2] - pieces[-1][0][1][0] <= reach:
            pieces[-1].append(word)
        else:
            pieces.append([word])
    return pieces


def _flat_line(ident: str, piece: list[tuple[str, Box, int]], geometry: LineGeometry) -> str:
    """One flat ocr_line for a run of words, on the baseline at its middle.

    OCRmyPDF's renderer reads the font size from the box top to the baseline,
    so the box takes the line's top and bottom edge at the same point.
    """
    left, right = piece[0][1][0], max(word_box[2] for _, word_box, _ in piece)
    middle = (left + right) / 2
    top, bottom = math.floor(geometry.top(middle)), math.ceil(geometry.bottom(middle))
    intercept = min(0, round(geometry.baseline(middle) - bottom))
    words = "\n".join(
        f'<span class="ocrx_word" id="word_{ident}_{i}" '
        f'title="{_bbox((word_box[0], top, word_box[2], bottom))}; '
        f'x_wconf {confidence}">{escape(text)}</span>'
        for i, (text, word_box, confidence) in enumerate(piece)
    )
    return (
        f'<span class="ocr_line" id="line_{ident}" title="{_bbox((left, top, right, bottom))}; '
        f'baseline 0 {intercept}">\n{words}\n</span>'
    )


def _bbox(box: Box) -> str:
    return f"bbox {box[0]} {box[1]} {box[2]} {box[3]}"


def advance(text: str) -> float:
    """Estimated advance width of `text` in em (NARROW_ADVANCE, AVERAGE_ADVANCE)."""
    return sum(NARROW_ADVANCE if char in NARROW else AVERAGE_ADVANCE for char in text)


def spaced_word_boxes(placed: Sequence[tuple[str, Box]], font_size: float) -> list[Box]:
    """Word boxes of one line that leave WORD_ROOM after each word.

    Inside the line a word keeps its left edge, and its right edge moves so
    that the space appended at the word's stretch ends WORD_ROOM font sizes
    before the next word. Recognizers also split a printed line into lines
    that touch or overlap on one baseline, so the line's first word starts
    and its last word ends WORD_ROOM (at most EDGE_ROOM_MAX of the word's
    width) inside their boxes. A word never passes the recognizer's own right
    edge, so a real gap stays a gap, and keeps at least one pixel; a word too
    short for its space pushes the next word's left edge on instead.
    """
    if not placed:
        return []
    room, space = WORD_ROOM * font_size, SPACE_ADVANCE * font_size
    lefts = [word_box[0] for _, word_box in placed]
    x0, _, x1, _ = placed[0][1]
    lefts[0] = min(math.ceil(x0 + min(room, EDGE_ROOM_MAX * (x1 - x0))), x1 - 1)
    boxes = []
    for i, (text, (original, top, x1, bottom)) in enumerate(placed):
        x0 = lefts[i]
        if i + 1 == len(placed):
            end = x1 - min(room, EDGE_ROOM_MAX * (x1 - original))
            boxes.append((x0, top, max(x0 + 1, math.floor(end)), bottom))
            break
        natural = advance(text) * font_size
        # right + space * (right - x0) / natural = lefts[i + 1] - room
        end = min(x1, (lefts[i + 1] - room + space * x0 / natural) / (1 + space / natural))
        right = max(x0 + 1, math.floor(end))
        boxes.append((x0, top, right, bottom))
        if right > end:
            following_x1 = placed[i + 1][1][2]
            lefts[i + 1] = max(lefts[i + 1], min(
                math.ceil(right + space * (right - x0) / natural + room), following_x1 - 1))
    return boxes


def _placed_words(
    line: TextLine,
    tokens: list[str],
    box: Box,
    font_size: float,
    width: int,
    page_height: int,
) -> list[tuple[str, Box, int]]:
    """The line's words with their boxes and x_wconf.

    Uses the recognizer's pieces when they spell the words exactly, merging
    character pieces per word and leaving room between words (WORD_ROOM).
    Otherwise the words are spread across the line box by character count,
    which leaves a gap between them; the spike (#62) showed that spread words
    glue together on skewed lines, so this is only the fallback for results
    without usable pieces.
    """
    grouped = _group_pieces(line.words, tokens, width, page_height)
    if grouped is not None:
        boxes = spaced_word_boxes([(text, word_box) for text, word_box, _ in grouped],
                                  font_size)
        return [(text, word_box, confidence)
                for (text, _, confidence), word_box in zip(grouped, boxes)]
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

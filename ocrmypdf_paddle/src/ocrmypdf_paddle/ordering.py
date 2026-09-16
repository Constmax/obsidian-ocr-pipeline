"""Explicit reading order for recognized lines (docs/paddle-textlayer.md, step 3).

Pure module: no OCRmyPDF, RapidOCR or numpy imports.

RapidOCR returns lines sorted by y, so on a two-column page the columns
alternate line by line. order_lines() rebuilds the reading order from line
geometry alone:

1. Estimate the page skew from long lines and measure every line in deskewed
   coordinates.
2. Cut off a header and a footer band at a horizontal gap that no line
   crosses, near the top or the bottom of the page.
3. Look for a gutter between 30 % and 70 % of the text width that almost no
   narrow line crosses, with lines side by side on both sides. On such a
   page the columns begin at the first column pair, so a running header
   closer above them than a gap still belongs to the header, and footer
   rows paired across the gutter (footnotes) go back to their columns.
4. A line crossing the gutter with no column line beside it is full width
   (a heading, a single-column paragraph, a footer) and separates the page
   into sections. A crossing line beside column lines (a note written into
   the gutter) belongs to the side of its centre.
5. Each section is read left column, then right column; full-width lines
   are read where they stand.
6. Within a column, and on a single-column page, lines are read in visual
   rows, top to bottom and left to right. Short lines standing in the margin
   beside the body text are read after that column, so they never split a
   sentence of the main text. Footnotes stay at the bottom of their column.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from ocrmypdf_paddle.hocr import Point, TextLine, clamp_polygon

#: A header gap must lie in this top share of the page, a footer gap in this
#: bottom share.
HEADER_SHARE = 0.22
FOOTER_SHARE = 0.12

#: Skew beyond this is left to OCRmyPDF's rotation and deskew.
MAX_SKEW = math.radians(10)

#: Resolution of the gutter search across the text width.
GUTTER_BINS = 200

#: Fewest lines on each side of a gutter.
MIN_COLUMN_LINES = 4

#: A right column line starting within this many line heights of the
#: column's text edge begins a column row.
ALIGN = 2.5


@dataclass(frozen=True)
class _Box:
    """A line's axis-aligned box in deskewed page coordinates."""

    index: int
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0


def order_lines(lines: Sequence[TextLine], width: int, height: int) -> list[TextLine]:
    """`lines` in reading order; always a permutation of the input.

    Lines without text or with an unusable polygon cannot be placed. They
    keep their relative order at the end; render_page() drops them anyway.
    """
    boxes, unplaced = _measure(lines, width, height)
    if len(boxes) < 2:
        return list(lines)
    h = statistics.median(box.h for box in boxes)
    header, body, footer = _bands(boxes, height, h)
    gutter = _gutter(body, h)
    if gutter is None:
        middle = _column(body, h)
    else:
        header, body, footer = _column_bands(boxes, header, body, footer, gutter, height, h)
        middle = _sections(body, gutter, h)
    ordered = _rows(header, h) + middle + _rows(footer, h)
    return [lines[box.index] for box in ordered] + [lines[i] for i in unplaced]


def _measure(
    lines: Sequence[TextLine], width: int, height: int
) -> tuple[list[_Box], list[int]]:
    placed: list[tuple[int, tuple[Point, ...]]] = []
    unplaced: list[int] = []
    for i, line in enumerate(lines):
        polygon = clamp_polygon(line.polygon, width, height)
        if polygon is None or not isinstance(line.text, str) or not line.text.strip():
            unplaced.append(i)
        else:
            placed.append((i, polygon))
    angle = _skew([polygon for _, polygon in placed])
    cos, sin = math.cos(-angle), math.sin(-angle)
    cx, cy = width / 2, height / 2
    boxes = []
    for i, polygon in placed:
        points = [
            ((x - cx) * cos - (y - cy) * sin + cx, (x - cx) * sin + (y - cy) * cos + cy)
            for x, y in polygon
        ]
        xs = [x for x, _ in points]
        ys = [y for _, y in points]
        boxes.append(_Box(i, min(xs), min(ys), max(xs), max(ys)))
    return boxes, unplaced


def _skew(polygons: Sequence[Sequence[Point]]) -> float:
    """Median direction of lines at least four times as long as they are thick."""
    angles = []
    for polygon in polygons:
        by_x = sorted(polygon)
        (ax, ay), (bx, by) = _midpoint(by_x[:2]), _midpoint(by_x[2:])
        length = math.hypot(bx - ax, by - ay)
        if length > 0 and length >= 4 * _area(polygon) / length:
            angles.append(math.atan2(by - ay, bx - ax))
    if len(angles) < 3:
        return 0.0
    angle = statistics.median(angles)
    return angle if abs(angle) <= MAX_SKEW else 0.0


def _midpoint(points: Sequence[Point]) -> Point:
    return (sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points))


def _area(polygon: Sequence[Point]) -> float:
    """Area of the polygon with its points taken in angular order."""
    cx, cy = _midpoint(polygon)
    ring = sorted(polygon, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    return abs(
        sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]))
    ) / 2


def _bands(
    boxes: list[_Box], height: int, h: float
) -> tuple[list[_Box], list[_Box], list[_Box]]:
    """(header, body, footer) split at horizontal gaps that no line crosses.

    A gap counts when it is at least one line height and half again the
    usual leading, so evenly spaced text has no header. The header ends at
    the lowest such gap in the top HEADER_SHARE of the page, holding at most
    a quarter of the lines. The footer starts at the lowest such gap in the
    bottom FOOTER_SHARE, holding at most a tenth of the lines. A gap between
    footnotes and body text runs through one column only, so per-column
    footnotes stay in their column.
    """
    by_top = sorted(boxes, key=lambda box: box.y0)
    n = len(by_top)
    header_end, footer_start = 0, n
    threshold = max(h, 1.5 * _leading(boxes))
    reach = by_top[0].y1
    gaps = []
    for k in range(1, n):
        if by_top[k].y0 - reach >= threshold:
            gaps.append((k, (reach + by_top[k].y0) / 2))
        reach = max(reach, by_top[k].y1)
    for k, middle in gaps:
        if middle <= HEADER_SHARE * height and k <= 0.25 * n:
            header_end = max(header_end, k)
        if middle >= (1 - FOOTER_SHARE) * height and n - k <= 0.1 * n:
            footer_start = k
    footer_start = max(footer_start, header_end)
    return by_top[:header_end], by_top[header_end:footer_start], by_top[footer_start:]


def _leading(boxes: list[_Box]) -> float:
    """Median space between a line and the nearest line below that shares its x range."""
    spaces = []
    for box in boxes:
        below = [
            other.y0 - box.y1
            for other in boxes
            if other.y0 >= box.cy and other.x0 < box.x1 and box.x0 < other.x1 and other is not box
        ]
        if below:
            spaces.append(max(0.0, min(below)))
    return statistics.median(spaces) if spaces else 0.0


def _column_bands(
    boxes: list[_Box],
    header: list[_Box],
    body: list[_Box],
    footer: list[_Box],
    gutter: float,
    height: int,
    h: float,
) -> tuple[list[_Box], list[_Box], list[_Box]]:
    """Header and footer bands of a two-column page, cut at its column pairs.

    Detected boxes overlap on densely set scans, so a running header can sit
    closer above the columns than any gap _bands() accepts. The header grows
    to the lowest gap between line cores above the first column pair, within
    the top HEADER_SHARE and a quarter of the lines.

    Footnotes starting at the same height in both columns leave a page-wide
    gap above them, which puts them into the footer. Footer rows holding a
    column pair or lines of one column only go back to the columns, so the
    footnotes stay at the bottom of their column; the footer starts at the
    first row crossing the gutter or pairing lines that are no column rows.
    """
    edge = _right_edge(body, gutter, h)
    pairs = _pairs(body, gutter, edge, h)
    if pairs:
        first = min(min(a.cy, b.cy) for a, b in pairs) - h / 4
        above = [
            cut
            for cut in _core_gaps(boxes, h)
            if cut <= min(first, HEADER_SHARE * height)
            and sum(box.cy < cut for box in boxes) <= 0.25 * len(boxes)
        ]
        if above:
            header = header + [box for box in body if box.cy < max(above)]
            body = [box for box in body if box.cy >= max(above)]
    rows = _row_groups(footer, h)
    count, paired = 0, False
    for row in rows:
        sides = [_side(box, gutter, h) for box in row]
        row_paired = bool(_pairs(row, gutter, edge, h))
        if None in sides or (len(set(sides)) == 2 and not row_paired):
            break
        count, paired = count + 1, paired or row_paired
    if paired:
        body = body + [box for row in rows[:count] for box in row]
        footer = [box for row in rows[count:] for box in row]
    return header, body, footer


def _side(box: _Box, gutter: float, h: float) -> str | None:
    """"L" or "R" for a line clear of the gutter, None for a line crossing it."""
    if box.x1 <= gutter + 0.25 * h:
        return "L"
    if box.x0 >= gutter - 0.25 * h:
        return "R"
    return None


def _right_edge(boxes: list[_Box], gutter: float, h: float) -> float:
    """Where the full lines of the right column start."""
    right = [box for box in boxes if _side(box, gutter, h) == "R"]
    if not right:
        return gutter
    span = max(box.x1 for box in right) - min(box.x0 for box in right)
    full = [box for box in right if box.w >= 0.7 * span] or right
    return statistics.median(box.x0 for box in full)


def _pairs(
    boxes: list[_Box], gutter: float, edge: float, h: float
) -> list[tuple[_Box, _Box]]:
    """Left and right column lines beside each other, the right one at the column edge.

    Hanging numerals and indents start within ALIGN line heights of the
    edge; the right part of a running header or footer is right-aligned or
    centred and starts well inside the column.
    """
    left = [box for box in boxes if _side(box, gutter, h) == "L"]
    right = [
        box for box in boxes if _side(box, gutter, h) == "R" and box.x0 <= edge + ALIGN * h
    ]
    return [(a, b) for b in right for a in left if _beside(a, b)]


def _core_gaps(boxes: list[_Box], h: float) -> list[float]:
    """Heights of horizontal gaps that no line's core crosses.

    A core is the middle half line height around a line's centre.
    """
    cores = sorted((box.cy - h / 4, box.cy + h / 4) for box in boxes)
    gaps = []
    reach = cores[0][1]
    for top, bottom in cores[1:]:
        if top > reach:
            gaps.append((reach + top) / 2)
        reach = max(reach, bottom)
    return gaps


def _overlap(a: _Box, b: _Box) -> float:
    return min(a.y1, b.y1) - max(a.y0, b.y0)


def _beside(a: _Box, b: _Box) -> bool:
    return _overlap(a, b) >= 0.5 * min(a.h, b.h)


def _gutter(boxes: list[_Box], h: float) -> float | None:
    """x position of a two-column gutter, or None for a single column."""
    if len(boxes) < 2 * MIN_COLUMN_LINES:
        return None
    left_edge = min(box.x0 for box in boxes)
    span = max(box.x1 for box in boxes) - left_edge
    narrow = [box for box in boxes if box.w < 0.6 * span]
    if span <= 0 or len(narrow) < 2 * MIN_COLUMN_LINES:
        return None
    size = span / GUTTER_BINS
    delta = [0] * (GUTTER_BINS + 1)
    for box in narrow:
        delta[max(0, int((box.x0 - left_edge) / size))] += 1
        delta[min(GUTTER_BINS, math.ceil((box.x1 - left_edge) / size))] -= 1
    coverage, running = [], 0
    for step in delta[:GUTTER_BINS]:
        running += step
        coverage.append(running)
    lo, hi = int(0.3 * GUTTER_BINS), int(0.7 * GUTTER_BINS)
    fewest = min(coverage[lo:hi])
    if fewest > max(1, 0.05 * len(narrow)):
        return None
    widest, start = (0, 0), None
    for i in range(lo, hi + 1):
        if i < hi and coverage[i] == fewest:
            start = i if start is None else start
        elif start is not None:
            if i - start > widest[1] - widest[0]:
                widest = (start, i)
            start = None
    if (widest[1] - widest[0]) * size < 0.5 * h:
        return None
    position = left_edge + (widest[0] + widest[1]) / 2 * size
    left = [box for box in narrow if box.x1 <= position + 0.25 * h]
    right = [box for box in narrow if box.x0 >= position - 0.25 * h]
    needed = max(MIN_COLUMN_LINES, 0.2 * len(narrow))
    if len(left) < needed or len(right) < needed:
        return None
    paired = sum(1 for a in left if any(_beside(a, b) for b in right))
    if paired < max(3, 0.3 * min(len(left), len(right))):
        return None
    return position


def _sections(boxes: list[_Box], gutter: float, h: float) -> list[_Box]:
    left, right, crossing = [], [], []
    for box in boxes:
        if box.x1 <= gutter + 0.25 * h:
            left.append(box)
        elif box.x0 >= gutter - 0.25 * h:
            right.append(box)
        else:
            crossing.append(box)
    columns = left + right
    separators = []
    for box in crossing:
        if any(_beside(box, other) for other in columns):
            (left if box.cx < gutter else right).append(box)
        else:
            separators.append(box)
    separators.sort(key=lambda box: (box.cy, box.x0))
    limits = [box.cy for box in separators]
    sections: list[tuple[list[_Box], list[_Box]]] = [([], []) for _ in range(len(separators) + 1)]
    for side, members in ((0, left), (1, right)):
        for box in members:
            sections[bisect.bisect_left(limits, box.cy)][side].append(box)
    ordered: list[_Box] = []
    for k, (left_part, right_part) in enumerate(sections):
        ordered += _column(left_part, h) + _column(right_part, h)
        if k < len(separators):
            ordered.append(separators[k])
    return ordered


def _column(boxes: list[_Box], h: float) -> list[_Box]:
    """Body lines in rows, then the lines standing in the margin beside them."""
    if len(boxes) < 3:
        return _rows(boxes, h)
    left_edge = min(box.x0 for box in boxes)
    span = max(box.x1 for box in boxes) - left_edge
    wide = [box for box in boxes if box.w >= 0.35 * span]
    rows = _row_groups(boxes, h)
    if not wide:
        return [box for row in rows for box in row]
    starts = sorted(box.x0 for box in wide)
    ends = sorted(box.x1 for box in wide)
    body_left = starts[len(starts) // 10]
    body_right = ends[len(ends) - 1 - len(ends) // 10]
    # A margin note stands clear of the body; a hanging numeral ("I.", "a)")
    # sits within two line heights of its text and stays in its row.
    notes = set()
    for row in rows:
        for i, box in enumerate(row):
            if box.w >= 0.35 * span:
                continue
            clear_right = i + 1 == len(row) or row[i + 1].x0 - box.x1 >= 2 * h
            clear_left = i == 0 or box.x0 - row[i - 1].x1 >= 2 * h
            if (box.x1 <= body_left + 0.25 * h and clear_right) or (
                box.x0 >= body_right - 0.25 * h and clear_left
            ):
                notes.add(box.index)
    body = [box for row in rows for box in row if box.index not in notes]
    return body + _rows([box for box in boxes if box.index in notes], h)


def _rows(boxes: list[_Box], h: float) -> list[_Box]:
    return [box for row in _row_groups(boxes, h) for box in row]


def _row_groups(boxes: list[_Box], h: float) -> list[list[_Box]]:
    """Visual rows top to bottom, each sorted left to right.

    A line joins the current row when its centre lies within 40 % of the
    smaller height from the centre of the row's first line.
    """
    rows: list[list[_Box]] = []
    for box in sorted(boxes, key=lambda box: (box.cy, box.x0)):
        if rows:
            first = rows[-1][0]
            if abs(box.cy - first.cy) <= 0.4 * min(box.h, first.h):
                rows[-1].append(box)
                continue
        rows.append([box])
    return [sorted(row, key=lambda box: box.x0) for row in rows]

"""hOCR conversion of the PaddleOCR engine: confidence, polygons, placement."""
import math
import random
import re
import xml.etree.ElementTree as ET

import pytest

from ocrmypdf_paddle.hocr import (
    BASELINE_SHARE,
    EDGE_ROOM_MAX,
    FLAT_SLOPE,
    WORD_ROOM,
    LineGeometry,
    TextLine,
    Word,
    bounding_box,
    clamp_polygon,
    render_page,
    wconf,
)

W, H = 1000, 800
XHTML = "{http://www.w3.org/1999/xhtml}"
# The patterns of OCRmyPDF 17.8.0's HocrParser (hocrtransform/hocr_parser.py).
# A slope in exponent notation or a fractional intercept would not match, and
# the parser would silently drop the baseline.
BASELINE = re.compile(r"baseline ([\-\+]?\d*\.?\d*) ([\-\+]?\d+)")
BBOX = re.compile(r"^bbox (\d+) (\d+) (\d+) (\d+)")
WCONF = re.compile(r"x_wconf (\d+)$")


def rect(x0, y0, x1, y1):
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def rotated(cx, cy, width, height, degrees):
    """Rectangle around (cx, cy) turned clockwise on screen (y points down)."""
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    corners = [(-width / 2, -height / 2), (width / 2, -height / 2),
               (width / 2, height / 2), (-width / 2, height / 2)]
    return tuple((cx + x * c - y * s, cy + x * s + y * c) for x, y in corners)


def parsed_lines(hocr):
    """[(line title, [(word text, word title), ...]), ...] in element order."""
    root = ET.fromstring(hocr)
    return [
        (span.get("title"),
         [(word.text, word.get("title")) for word in span if word.get("class") == "ocrx_word"])
        for span in root.iter(f"{XHTML}span") if span.get("class") == "ocr_line"
    ]


def word_box(title):
    return tuple(int(v) for v in BBOX.match(title).groups())


# ── Confidence ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("score, expected", [
    (0.0, 0), (1.0, 100), (0.874, 87), (0.06, 6),
    (1.3, 100), (-0.2, 0),  # clamped, not rejected
    (math.nan, 0), (math.inf, 0), (None, 0), ("junk", 0),
])
def test_wconf_maps_scores_to_0_100_without_cutoff(score, expected):
    assert wconf(score) == expected


def test_low_confidence_lines_are_kept():
    hocr, text = render_page([TextLine("faint", rect(10, 10, 200, 40), 0.01)], W, H)
    assert text == "faint\n"
    assert parsed_lines(hocr)[0][1][0][1].endswith("x_wconf 1")


# ── Polygons ───────────────────────────────────────────────────────────────


def test_clamp_polygon_keeps_valid_quads_and_clamps_to_the_image():
    assert clamp_polygon(rect(10, 20, 110, 50), W, H) == rect(10.0, 20.0, 110.0, 50.0)
    assert clamp_polygon(((-5, -3), (1200, -3), (1200, 40), (-5, 40)), W, H) == rect(
        0.0, 0.0, 1000.0, 40.0)


@pytest.mark.parametrize("points", [
    None,
    "abcd",
    [(0, 0), (10, 0), (10, 10)],                      # three points
    [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)],     # five points
    [(0, 0), (10, 0, 1), (10, 10), (0, 10)],          # a point with three values
    [(0, 0), (math.nan, 0), (10, 10), (0, 10)],
    [(0, 0), (math.inf, 0), (10, 10), (0, 10)],
    [("a", 0), (10, 0), (10, 10), (0, 10)],
    rect(10, 20, 110, 20),                            # no height
    [(0, 0), (10, 10), (20, 20), (30, 30)],           # collinear diagonal
    rect(1100, 10, 1200, 40),                         # entirely right of the image
])
def test_clamp_polygon_rejects_unusable_boxes(points):
    assert clamp_polygon(points, W, H) is None


def test_bounding_box_rounds_outwards():
    assert bounding_box(rect(10.2, 20.7, 110.1, 50.5)) == (10, 20, 111, 51)


def test_baseline_of_an_upright_line_lies_at_its_share_of_the_height():
    geometry = LineGeometry(rect(10.0, 20.0, 110.0, 60.0))
    assert geometry.baseline(10) == geometry.baseline(110) == pytest.approx(20 + 40 * BASELINE_SHARE)
    assert geometry.slope == 0
    assert geometry.font_size == pytest.approx(40 * BASELINE_SHARE)


def test_baseline_follows_a_skewed_line_in_any_point_order():
    polygon = clamp_polygon(rotated(500, 400, 600, 40, 4), W, H)
    geometry = LineGeometry(polygon)

    assert geometry.slope == pytest.approx(math.tan(math.radians(4)))
    # Across the line, the baseline lies BASELINE_SHARE of the way down.
    top_left, bottom_left = polygon[0], polygon[3]
    assert geometry.baseline(top_left[0]) == pytest.approx(
        geometry.top(top_left[0]) + BASELINE_SHARE * (
            geometry.bottom(top_left[0]) - geometry.top(top_left[0])))
    assert geometry.bottom(bottom_left[0]) == pytest.approx(bottom_left[1])

    shuffled = list(polygon)
    random.Random(7).shuffle(shuffled)
    other = LineGeometry(tuple(shuffled))
    assert (other.slope, other.baseline(300)) == (geometry.slope, geometry.baseline(300))


# ── Page rendering ─────────────────────────────────────────────────────────


def test_text_and_hocr_follow_the_given_line_order():
    lines = [
        TextLine("zweite Zeile", rect(100, 300, 400, 330), 0.9),
        TextLine("erste Zeile", rect(100, 100, 400, 130), 0.9),
        TextLine("rechte Spalte", rect(600, 50, 900, 80), 0.9),
    ]
    hocr, text = render_page(lines, W, H)

    assert text == "zweite Zeile\nerste Zeile\nrechte Spalte\n"
    words = [w for _, line_words in parsed_lines(hocr) for w, _ in line_words]
    assert words == text.splitlines()


def test_unplaceable_lines_are_left_out_of_hocr_and_text():
    lines = [
        TextLine("", rect(10, 10, 100, 40), 0.9),
        TextLine("   ", rect(10, 50, 100, 80), 0.9),
        TextLine("kaputt", ((0, 0), (1, 1)), 0.9),
        TextLine("bleibt", rect(10, 90, 100, 120), 0.9),
    ]
    hocr, text = render_page(lines, W, H)

    assert text == "bleibt\n"
    assert [[w for w, _ in words] for _, words in parsed_lines(hocr)] == [["bleibt"]]


def test_an_empty_page_is_valid_hocr_without_text():
    hocr, text = render_page([], W, H)
    assert text == ""
    root = ET.fromstring(hocr)
    page = next(div for div in root.iter(f"{XHTML}div") if div.get("class") == "ocr_page")
    assert page.get("title") == f"bbox 0 0 {W} {H}"


def test_markup_in_recognized_text_is_escaped():
    hocr, text = render_page([TextLine('a<b & "c" §', rect(10, 10, 300, 40), 0.9)], W, H)
    assert text == 'a<b & "c" §\n'
    assert [w for w, _ in parsed_lines(hocr)[0][1]] == ['a<b & "c" §']


def test_titles_use_the_formats_ocrmypdf_parses():
    nearly_flat = ((10.0, 10.0), (910.0, 10.0), (910.0, 40.0001), (10.0, 40.0))
    skewed = clamp_polygon(rotated(500, 400, 600, 40, -3), W, H)
    hocr, _ = render_page(
        [TextLine("fast flach", nearly_flat, 0.5), TextLine("schief", skewed, 0.75)], W, H)

    for title, words in parsed_lines(hocr):
        assert BBOX.match(title)
        match = BASELINE.search(title)
        assert match and match.group(0) == title.split("; ")[1]
        assert re.fullmatch(r"0|-?0\.\d+", match.group(1))
        for _, word_title in words:
            assert BBOX.match(word_title) and WCONF.search(word_title)
    assert "e-" not in hocr


# ── Line placement ─────────────────────────────────────────────────────────


def line_font_size(title):
    """Font size OCRmyPDF's renderer derives: box top to baseline."""
    _, top, _, bottom = word_box(title)
    return bottom + int(BASELINE.search(title).group(2)) - top


def test_a_line_is_one_piece_whatever_word_boxes_the_recognizer_gave():
    line = TextLine("Satz  eins BGB", rect(100, 100, 400, 140), 0.9, words=(
        Word("Satz", rect(101, 102, 180, 138), 0.8),
        Word("eins", rect(190, 101, 300, 139), 0.6),
        Word("BGB", rect(300, 101, 400, 139), 0.6),
    ))
    hocr, _ = render_page([line], W, H)

    (title, words), = parsed_lines(hocr)
    # One word holds the line, WORD_ROOM inside the box at either end.
    assert [t for t, _ in words] == ["Satz eins BGB"]
    assert word_box(words[0][1]) == word_box(title) == (108, 100, 392, 140)
    assert line_font_size(title) == 30
    assert WCONF.search(words[0][1]).group(1) == "90"


def test_a_skewed_line_keeps_its_slope_and_stays_one_piece():
    polygon = ((100.0, 301.0), (900.0, 309.0), (900.0, 349.0), (100.0, 341.0))
    geometry = LineGeometry(polygon)
    hocr, _ = render_page([TextLine("eins zwei drei vier", polygon, 0.9)], W, H)

    (title, words), = parsed_lines(hocr)
    left, _, _, bottom = word_box(title)
    assert len(words) == 1
    assert float(BASELINE.search(title).group(1)) == pytest.approx(0.01, abs=1e-4)
    # OCRmyPDF turns the line about the baseline at the box's left edge.
    assert bottom + int(BASELINE.search(title).group(2)) == pytest.approx(
        geometry.baseline(left), abs=1)
    assert line_font_size(title) == pytest.approx(geometry.font_size, abs=1)


def test_a_line_below_the_renderers_slope_floor_lies_flat_on_its_middle():
    slope = FLAT_SLOPE * 0.8
    polygon = ((100.0, 300.0), (900.0, 300 + 800 * slope), (900.0, 340 + 800 * slope),
               (100.0, 340.0))
    geometry = LineGeometry(polygon)
    hocr, _ = render_page([TextLine("eins zwei", polygon, 0.9)], W, H)

    (title, _), = parsed_lines(hocr)
    _, _, _, bottom = word_box(title)
    assert BASELINE.search(title).group(1) == "0"
    assert bottom + int(BASELINE.search(title).group(2)) == pytest.approx(
        geometry.baseline(500), abs=1)


@pytest.mark.parametrize("text, width", [("I", 16), ("§", 18), ("a)", 25)])
def test_a_short_word_alone_on_its_line_keeps_most_of_its_box(text, width):
    polygon = rect(100, 100, 100 + width, 140)
    hocr, _ = render_page([TextLine(text, polygon, 0.9, words=(Word(text, polygon, 0.9),))], W, H)

    x0, _, x1, _ = word_box(parsed_lines(hocr)[0][1][0][1])
    assert x1 - x0 >= (1 - 2 * EDGE_ROOM_MAX) * width - 2


@pytest.mark.parametrize("polygon", [
    ((1000.0, 0.0), (1000.0, 0.0), (1000.0, 9.8), (973.1, 0.0)),  # font size 0 before
    ((0.0, 180.0), (0.0, 168.1), (5.2, 240.6), (0.0, 252.5)),  # negative before
    ((0.0, 529.9), (433.0, 800.0), (384.0, 800.0), (0.0, 552.1)),
])
def test_slivers_clipped_at_the_page_edge_render_upright_boxes(polygon):
    assert clamp_polygon(polygon, W, H) == polygon
    assert LineGeometry(polygon).font_size > 0
    line = TextLine("ab cd", polygon, 0.9, words=(Word("ab", polygon, 0.9),
                                                  Word("cd", polygon, 0.9)))
    hocr, _ = render_page([line], W, H)

    for title, words in parsed_lines(hocr):
        for t in (title, *(word_title for _, word_title in words)):
            x0, top, x1, bottom = word_box(t)
            assert x0 < x1 and top < bottom
        assert line_font_size(title) > 0

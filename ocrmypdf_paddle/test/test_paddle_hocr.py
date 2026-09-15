"""hOCR conversion of the PaddleOCR engine: confidence, polygons, placement."""
import math
import random
import re
import xml.etree.ElementTree as ET

import pytest

from ocrmypdf_paddle.hocr import (
    TextLine,
    Word,
    baseline,
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


def test_baseline_of_an_upright_line_is_flat_on_the_box_bottom():
    polygon = rect(10.0, 20.0, 110.0, 50.0)
    assert baseline(polygon, bounding_box(polygon)) == (0.0, 0)


def test_baseline_follows_the_bottom_edge_of_a_skewed_line():
    polygon = clamp_polygon(rotated(500, 400, 600, 40, 4), W, H)
    box = bounding_box(polygon)
    slope, intercept = baseline(polygon, box)

    assert slope == pytest.approx(math.tan(math.radians(4)))
    # Bottom-left corner of the turned rectangle, moved to the box's left edge.
    bottom_left = polygon[3]
    expected = bottom_left[1] + slope * (box[0] - bottom_left[0]) - box[3]
    assert intercept == round(expected)
    assert intercept < 0

    shuffled = list(polygon)
    random.Random(7).shuffle(shuffled)
    assert baseline(tuple(shuffled), box) == (slope, intercept)


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
    assert words == text.split()


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
    assert [w for w, _ in parsed_lines(hocr)[0][1]] == ["a<b", "&", '"c"', "§"]


def test_titles_use_the_formats_ocrmypdf_parses():
    nearly_flat = ((10.0, 10.0), (910.0, 10.0), (910.0, 40.0001), (10.0, 40.0))
    skewed = clamp_polygon(rotated(500, 400, 600, 40, -3), W, H)
    hocr, _ = render_page(
        [TextLine("fast flach", nearly_flat, 0.5), TextLine("schief", skewed, 0.75)], W, H)

    for title, words in parsed_lines(hocr):
        assert BBOX.match(title)
        match = BASELINE.search(title)
        assert match and match.group(0) == title.split("; ")[1]
        for _, word_title in words:
            assert BBOX.match(word_title) and WCONF.search(word_title)
    assert "e-" not in hocr


# ── Word placement ─────────────────────────────────────────────────────────


def test_word_pieces_that_spell_the_line_give_the_word_boxes():
    line = TextLine("Satz eins", rect(100, 100, 400, 130), 0.9, words=(
        Word("Satz", rect(101, 102, 180, 128), 0.8),
        Word("eins", rect(210, 101, 300, 129), 0.6),
    ))
    hocr, _ = render_page([line], W, H)

    words = parsed_lines(hocr)[0][1]
    assert [(t, word_box(title)) for t, title in words] == [
        ("Satz", (101, 102, 180, 128)), ("eins", (210, 101, 300, 129))]
    assert [WCONF.search(title).group(1) for _, title in words] == ["80", "60"]


def test_character_pieces_are_merged_per_word():
    pieces = tuple(
        Word(ch, rect(100 + 20 * i, 100, 118 + 20 * i, 130), score)
        for i, (ch, score) in enumerate(zip("Grünab", (0.9, 0.9, 0.6, 0.9, 0.8, 0.8)))
    )
    line = TextLine("Grün ab", rect(100, 100, 220, 130), 0.9, words=pieces)
    hocr, _ = render_page([line], W, H)

    words = parsed_lines(hocr)[0][1]
    assert [(t, word_box(title)) for t, title in words] == [
        ("Grün", (100, 100, 178, 130)), ("ab", (180, 100, 218, 130))]
    assert [WCONF.search(title).group(1) for _, title in words] == ["82", "80"]


@pytest.mark.parametrize("pieces", [
    (Word("Grun", rect(100, 100, 180, 130), 0.9), Word("ab", rect(190, 100, 220, 130), 0.9)),
    (Word("Grün", rect(100, 100, 180, 130), 0.9),),                       # a word missing
    (Word("Grün", rect(100, 100, 180, 130), 0.9), Word("ab", rect(190, 100, 220, 130), 0.9),
     Word("x", rect(230, 100, 240, 130), 0.9)),                           # a piece left over
    (Word("Grün", ((0, 0), (1, 1)), 0.9), Word("ab", rect(190, 100, 220, 130), 0.9)),
])
def test_unusable_pieces_fall_back_to_spreading_words_across_the_line(pieces):
    line = TextLine("Grün ab", rect(100, 100, 220, 130), 0.7, words=pieces)
    hocr, _ = render_page([line], W, H)

    words = parsed_lines(hocr)[0][1]
    boxes = [word_box(title) for _, title in words]
    assert [t for t, _ in words] == ["Grün", "ab"]
    assert boxes[0][0] == 100 and boxes[-1][2] <= 220
    assert boxes[0][2] <= boxes[1][0]
    assert all((b[1], b[3]) == (100, 130) for b in boxes)
    assert all(title.endswith("x_wconf 70") for _, title in words)

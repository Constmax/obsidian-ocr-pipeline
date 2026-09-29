"""hOCR conversion of the PaddleOCR engine: confidence, polygons, placement."""
import math
import random
import re
import xml.etree.ElementTree as ET

import pytest

from ocrmypdf_paddle.hocr import (
    AVERAGE_ADVANCE,
    BASELINE_SHARE,
    EDGE_ROOM_MAX,
    FLAT_DRIFT,
    NARROW_ADVANCE,
    SPACE_ADVANCE,
    WORD_ROOM,
    LineGeometry,
    TextLine,
    Word,
    advance,
    bounding_box,
    clamp_polygon,
    render_page,
    spaced_word_boxes,
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
        assert match.group(1) == "0"
        for _, word_title in words:
            assert BBOX.match(word_title) and WCONF.search(word_title)
    assert "e-" not in hocr


# ── Word placement ─────────────────────────────────────────────────────────


def line_font_size(title):
    """Font size OCRmyPDF's renderer derives: box top to baseline."""
    _, top, _, bottom = word_box(title)
    return bottom + int(BASELINE.search(title).group(2)) - top


def test_word_pieces_that_spell_the_line_give_the_word_boxes():
    line = TextLine("Satz eins", rect(100, 100, 400, 140), 0.9, words=(
        Word("Satz", rect(101, 102, 180, 138), 0.8),
        Word("eins", rect(190, 101, 300, 139), 0.6),
    ))
    hocr, _ = render_page([line], W, H)

    title, words = parsed_lines(hocr)[0]
    boxes = [word_box(t) for _, t in words]
    assert [t for t, _ in words] == ["Satz", "eins"]
    # The line's words start and end WORD_ROOM inside their boxes; the first
    # one also ends early enough for its space. Words take the line's height.
    assert boxes[0][0] == 101 + round(WORD_ROOM * 30) and boxes[0][2] < 180
    assert boxes[1] == (190, 100, 300 - round(WORD_ROOM * 30), 140)
    assert line_font_size(title) == 30
    assert [WCONF.search(t).group(1) for _, t in words] == ["80", "60"]


def test_room_after_each_rendered_space_is_the_word_room():
    font_size = 40
    placed = [("Wagen", (100, 0, 330, 50)), ("im", (338, 0, 430, 50)),
              ("Auftrag", (438, 0, 700, 50))]
    boxes = spaced_word_boxes(placed, font_size)

    assert boxes[0][0] == 100 + WORD_ROOM * font_size
    for (text, _), box, following in zip(placed, boxes, boxes[1:]):
        stretch = (box[2] - box[0]) / (advance(text) * font_size)
        space_end = box[2] + SPACE_ADVANCE * font_size * stretch
        assert (following[0] - space_end) / font_size == pytest.approx(WORD_ROOM, abs=0.05)
    assert boxes[-1] == (438, 0, 700 - WORD_ROOM * font_size, 50)


def test_word_boxes_never_grow_into_a_real_gap():
    boxes = spaced_word_boxes([("K", (100, 0, 130, 50)), ("weit", (600, 0, 700, 50))], 40)
    # The first word starts WORD_ROOM inside, but by at most EDGE_ROOM_MAX of 30 px.
    assert boxes[0] == (109, 0, 130, 50)


def test_a_word_too_short_for_its_space_pushes_the_next_word_on():
    font_size = 40
    boxes = spaced_word_boxes([("I", (100, 0, 104, 50)), ("x", (105, 0, 120, 50))], font_size)
    # "I" keeps one pixel; "x" starts after its space and the word room.
    assert boxes[0] == (102, 0, 103, 50)
    space_end = 103 + SPACE_ADVANCE * font_size * 1 / (advance("I") * font_size)
    assert boxes[1][0] - space_end >= WORD_ROOM * font_size
    assert boxes[1][2] > boxes[1][0]


def test_narrow_characters_count_narrower():
    # A flat average stretched "ist" further than modeled, leaving pdftotext no room.
    assert advance("ist") == pytest.approx(2 * NARROW_ADVANCE + AVERAGE_ADVANCE)
    assert advance("Wagen") == pytest.approx(5 * AVERAGE_ADVANCE)


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


def test_character_pieces_are_merged_per_word():
    pieces = tuple(
        Word(ch, rect(100 + 20 * i, 100, 118 + 20 * i, 130), score)
        for i, (ch, score) in enumerate(zip("Grünab", (0.9, 0.9, 0.6, 0.9, 0.8, 0.8)))
    )
    line = TextLine("Grün ab", rect(100, 100, 220, 130), 0.9, words=pieces)
    hocr, _ = render_page([line], W, H)

    words = parsed_lines(hocr)[0][1]
    # The unions (100-178, 180-218) nearly touch; the first word gives way.
    assert [(t, word_box(title)[0]) for t, title in words] == [("Grün", 106), ("ab", 180)]
    assert word_box(words[0][1])[2] < 170
    assert [WCONF.search(title).group(1) for _, title in words] == ["82", "80"]


def test_a_skewed_line_is_written_as_flat_pieces_along_its_baseline():
    words, x, slope = [], 100, 0.01
    for token in "eins zwei drei vier fünf sechs sieben acht".split():
        x1 = x + 100
        words.append(Word(token, ((x, 300 + slope * x), (x1, 300 + slope * x1),
                                  (x1, 340 + slope * x1), (x, 340 + slope * x)), 0.9))
        x = x1 + 10
    polygon = (words[0].polygon[0], words[-1].polygon[1], words[-1].polygon[2],
               words[0].polygon[3])
    line = TextLine(" ".join(w.text for w in words), polygon, 0.9, tuple(words))
    geometry = LineGeometry(polygon)
    hocr, text = render_page([line], W, H)

    pieces = parsed_lines(hocr)
    assert len(pieces) > 1
    assert [t for _, ws in pieces for t, _ in ws] == text.split()
    for title, piece_words in pieces:
        left, top, right, bottom = word_box(title)
        assert BASELINE.search(title).group(1) == "0"
        base = bottom + int(BASELINE.search(title).group(2))
        # Flat, yet on the real baseline within half the allowed drift.
        for x in (left, right):
            assert abs(base - geometry.baseline(x)) <= FLAT_DRIFT * geometry.font_size / 2 + 1
        assert line_font_size(title) == pytest.approx(geometry.font_size, abs=2)


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

"""order_lines(): reading order from line geometry alone (pure, no OCRmyPDF).

Pages are A4 at 300 dpi. Input lines arrive the way RapidOCR returns them,
sorted by y, so two columns alternate unless ordering rebuilds them.
"""
import math
from collections import Counter

from ocrmypdf_paddle.hocr import TextLine
from ocrmypdf_paddle.ordering import order_lines

W, H = 2480, 3508
LINE = 40  # line height
STEP = 60  # line pitch
LEFT = (200, 1180)
RIGHT = (1300, 2280)


def box(text, x0, y0, x1, height=LINE):
    return TextLine(text, ((x0, y0), (x1, y0), (x1, y0 + height), (x0, y0 + height)), 0.9)


def column(prefix, span, top, count):
    return [box(f"{prefix}{i}", span[0], top + i * STEP, span[1]) for i in range(count)]


def as_recognized(lines):
    """RapidOCR's order: by top edge, then left edge."""
    return sorted(lines, key=lambda line: (line.polygon[0][1], line.polygon[0][0]))


def texts(lines):
    return [line.text for line in lines]


def names(prefix, count):
    return [f"{prefix}{i}" for i in range(count)]


def rotated(lines, degrees):
    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)
    cx, cy = W / 2, H / 2
    return [
        TextLine(line.text, tuple(
            ((x - cx) * cos - (y - cy) * sin + cx, (x - cx) * sin + (y - cy) * cos + cy)
            for x, y in line.polygon
        ), line.confidence)
        for line in lines
    ]


def test_single_column_is_read_top_to_bottom():
    lines = column("z", (200, 2280), 400, 12)
    assert texts(order_lines(list(reversed(lines)), W, H)) == names("z", 12)


def test_two_columns_are_read_left_then_right():
    lines = column("L", LEFT, 400, 20) + column("R", RIGHT, 400, 20)
    assert texts(order_lines(as_recognized(lines), W, H)) == names("L", 20) + names("R", 20)


def test_full_width_heading_between_column_regions():
    top = column("La", LEFT, 400, 10) + column("Ra", RIGHT, 400, 10)
    heading = box("Zwischentitel", 800, 1040, 1680, height=50)
    bottom = column("Lb", LEFT, 1160, 10) + column("Rb", RIGHT, 1160, 10)
    ordered = order_lines(as_recognized(top + [heading] + bottom), W, H)
    assert texts(ordered) == (
        names("La", 10) + names("Ra", 10) + ["Zwischentitel"] + names("Lb", 10) + names("Rb", 10)
    )


def test_four_full_width_headings_between_column_regions():
    # Four centred headings cross the gutter, just under 5 % of the 84 narrow
    # lines. Leaving the header and footer shares out of the crossing count
    # must not shrink that tolerance.
    lines, expected = [], []
    for k in range(5):
        top = 300 + k * 600
        lines += column(f"L{k}_", LEFT, top, 8) + column(f"R{k}_", RIGHT, top, 8)
        expected += names(f"L{k}_", 8) + names(f"R{k}_", 8)
        if k < 4:
            lines.append(box(f"Titel{k}", 900, top + 500, 1580, height=50))
            expected.append(f"Titel{k}")
    assert texts(order_lines(as_recognized(lines), W, H)) == expected


def test_single_column_paragraph_above_two_columns():
    intro = column("S", (200, 2280), 400, 6)
    columns = column("L", LEFT, 780, 15) + column("R", RIGHT, 780, 15)
    ordered = order_lines(as_recognized(intro + columns), W, H)
    assert texts(ordered) == names("S", 6) + names("L", 15) + names("R", 15)


def test_full_width_footer_crossing_the_gutter_is_read_last():
    lines = column("L", LEFT, 400, 20) + column("R", RIGHT, 400, 20)
    footer = box("Fusszeile", 900, 3300, 1580)
    ordered = order_lines(as_recognized(lines + [footer]), W, H)
    assert texts(ordered) == names("L", 20) + names("R", 20) + ["Fusszeile"]


def test_page_number_below_the_left_column_is_read_last():
    lines = column("L", LEFT, 2000, 20) + column("R", RIGHT, 2000, 20)
    number = box("7", 300, 3350, 340)
    ordered = order_lines(as_recognized(lines + [number]), W, H)
    assert texts(ordered) == names("L", 20) + names("R", 20) + ["7"]


def test_header_band_is_read_before_the_columns():
    header = [
        box("Logo", 200, 150, 900, height=80),
        *(box(f"Ort{i}", 1000, 150 + i * 35, 2280, height=30) for i in range(3)),
        box("Rubrik", 200, 300, 600),
        box("Seite", 1900, 300, 2280),
    ]
    lines = column("L", LEFT, 460, 20) + column("R", RIGHT, 460, 20)
    ordered = texts(order_lines(as_recognized(header + lines), W, H))
    assert sorted(ordered[:6]) == sorted(texts(header))
    assert ordered.index("Rubrik") < ordered.index("Seite")
    assert ordered[6:] == names("L", 20) + names("R", 20)


def test_dense_running_header_is_read_before_the_columns():
    # The header's last row sits less than one line height above the columns,
    # and its location list crosses the gutter.
    header = [
        box("Logo", 200, 150, 900, height=90),
        *(box(f"Ort{i}", 1000, 150 + i * 35, 2280, height=40) for i in range(4)),
        box("Rubrik", 200, 300, 600, height=50),
        box("Titel, Seite 7", 1800, 300, 2280, height=50),
    ]
    lines = column("L", LEFT, 370, 25) + column("R", RIGHT, 370, 25)
    ordered = texts(order_lines(as_recognized(header + lines), W, H))
    assert sorted(ordered[:7]) == sorted(texts(header))
    assert ordered.index("Rubrik") < ordered.index("Titel, Seite 7")
    assert ordered[7:] == names("L", 25) + names("R", 25)


def test_dense_two_part_running_header_is_read_before_the_columns():
    # No header line crosses the gutter, and the tall header boxes leave no
    # gap _bands() accepts. The row stands 1.7 line pitches above the
    # columns, as on the course pages (n01, t03), not one pitch like a row.
    header = [box("Rubrik", 200, 245, 600, height=90),
              box("Titel, Seite 7", 1800, 245, 2280, height=90)]
    lines = column("L", LEFT, 370, 25) + column("R", RIGHT, 370, 25)
    ordered = texts(order_lines(as_recognized(header + lines), W, H))
    assert ordered == ["Rubrik", "Titel, Seite 7"] + names("L", 25) + names("R", 25)


def test_footnotes_starting_level_in_both_columns_stay_in_their_column():
    # The gap above the footnotes runs across the page in the bottom band.
    left = column("L", LEFT, 1400, 30) + [box(f"FL{i}", 200, 3250 + i * 45, 1180, height=30)
                                          for i in range(3)]
    right = column("R", RIGHT, 1400, 30) + [box(f"FR{i}", 1300, 3250 + i * 45, 2000, height=30)
                                            for i in range(2)]
    footer = box("Fusszeile", 900, 3385, 1580)
    ordered = order_lines(as_recognized(left + right + [footer]), W, H)
    assert texts(ordered) == (
        names("L", 30) + names("FL", 3) + names("R", 30) + names("FR", 2) + ["Fusszeile"]
    )


def test_footnotes_offset_by_half_a_line_stay_in_their_column():
    # Both columns' footnotes start level but are offset by half a line, so
    # no row holds a left and a right footnote (n07). The leading footer rows
    # still pair across the gutter as a whole.
    left = column("L", LEFT, 1400, 30) + [box(f"FL{i}", 200, 3250 + i * 45, 1180, height=30)
                                          for i in range(3)]
    right = column("R", RIGHT, 1400, 30) + [box(f"FR{i}", 1300, 3264 + i * 45, 2000, height=30)
                                            for i in range(2)]
    footer = box("Fusszeile", 900, 3385, 1580)
    ordered = order_lines(as_recognized(left + right + [footer]), W, H)
    assert texts(ordered) == (
        names("L", 30) + names("FL", 3) + names("R", 30) + names("FR", 2) + ["Fusszeile"]
    )


def test_sparse_page_with_running_header_and_footer_in_the_body_keeps_its_columns():
    # Large type, few lines (m06): the boxes touch, so _bands() cuts off
    # neither the running header nor the footer. The location list and the
    # footer cross the gutter, more of them than 5 % of the narrow lines.
    big, pitch = 64, 70
    header = [
        box("Logo", 180, 80, 1140, height=90),
        *(box(f"Ort{i}", 1150, 65 + i * 40, 2410, height=75) for i in range(5)),
        box("Rubrik", 340, 344, 700, height=68),
        box("Titel, Seite 13", 1785, 360, 2230, height=64),
    ]
    left = [box(f"L{i}", 330, 432 + i * pitch, 1180, height=big) for i in range(38)]
    right = [box(f"R{i}", 1300, 432 + i * pitch, 2200, height=big) for i in range(38)]
    notes_left = [box(f"FL{i}", 330, 3100 + i * 90, 1180, height=70) for i in range(3)]
    notes_right = [box(f"FR{i}", 1300, 3100 + i * 90, 2000, height=70) for i in range(2)]
    footer = box("Fusszeile", 1177, 3397, 1354, height=48)
    lines = header + left + right + notes_left + notes_right + [footer]
    ordered = texts(order_lines(as_recognized(lines), W, H))
    assert sorted(ordered[:8]) == sorted(texts(header))
    assert ordered[8:] == (
        names("L", 38) + names("FL", 3) + names("R", 38) + names("FR", 2) + ["Fusszeile"]
    )


def test_indented_first_column_row_is_not_a_header():
    # The top right body line starts more than ALIGN line heights inside the
    # right column edge, so the first column pair drops a row. The grown rows
    # hold no line crossing the gutter, so they stay the first body row.
    lines = column("L", LEFT, 400, 10) + column("R", RIGHT, 400, 10)
    lines[10] = box("R0", RIGHT[0] + 3 * LINE, 400, RIGHT[1])
    ordered = order_lines(as_recognized(lines), W, H)
    assert texts(ordered) == names("L", 10) + names("R", 10)


def words(prefix, span, top, count, parts=6):
    """A column recognized in words, each line cut into `parts` boxes 2 px apart in height."""
    size = (span[1] - span[0]) / parts
    return [
        box(f"{prefix}{i}_{k}", span[0] + k * size, top + i * STEP + 2 * k,
            span[0] + (k + 1) * size - 20)
        for i in range(count)
        for k in range(parts)
    ]


def test_tilted_two_part_running_header_is_read_before_the_columns():
    # The rubric row's right part sits half a line lower than its left part
    # (q07): from the lower part the row stands less than half again the
    # line pitch above the columns, from the upper part more.
    header = [box("Rubrik", 270, 278, 720, height=64),
              box("Fall 2, Seite 3", 1800, 308, 2280, height=64)]
    lines = column("L", LEFT, 390, 25) + column("R", RIGHT, 390, 25)
    ordered = texts(order_lines(as_recognized(header + lines), W, H))
    assert ordered == ["Rubrik", "Fall 2, Seite 3"] + names("L", 25) + names("R", 25)


def test_right_column_in_single_words_keeps_its_edge():
    # The right column is recognized in thirds of a line (q06), so no box is
    # a full line and the median start lies inside the column. Its edge
    # stays where its lines start, and the right-aligned header part is no
    # column line pairing with the label.
    header = [box("Rubrik", 200, 245, 600, height=90),
              box("Titel, Seite 7", 1700, 245, 2280, height=90)]
    lines = column("L", LEFT, 370, 25) + words("R", RIGHT, 370, 25, parts=3)
    ordered = texts(order_lines(as_recognized(header + lines), W, H))
    right = [f"R{i}_{k}" for i in range(25) for k in range(3)]
    assert ordered == ["Rubrik", "Titel, Seite 7"] + names("L", 25) + right


def test_indented_first_column_row_beside_a_column_in_words_is_not_a_header():
    # As test_indented_first_column_row_is_not_a_header, with the left column
    # recognized in parts of a line a few pixels apart in height. The line
    # pitch is measured between rows, not between those parts.
    lines = words("L", LEFT, 400, 10, parts=3) + column("R", RIGHT, 400, 10)
    lines[30] = box("R0", RIGHT[0] + 3 * LINE, 400, RIGHT[1])
    ordered = texts(order_lines(as_recognized(lines), W, H))
    left = [f"L{i}_{k}" for i in range(10) for k in range(3)]
    assert ordered == left + names("R", 10)


def test_footnotes_of_the_left_column_without_a_footer_stay_in_their_column():
    # The page is cut off below the left column's footnotes (q03): the lowest
    # page-wide gap lies above them, so they form the footer band on their own.
    left = column("L", LEFT, 1400, 30)
    notes = [box("7", 150, 3250, 180, height=30), box("FL0", 200, 3250, 900, height=30),
             box("FL1", 200, 3295, 1100, height=30)]
    right = column("R", RIGHT, 1400, 30)
    ordered = order_lines(as_recognized(left + notes + right), W, H)
    assert texts(ordered) == names("L", 30) + ["7", "FL0", "FL1"] + names("R", 30)


def test_tall_first_heading_beside_an_indented_right_line_is_not_a_header():
    # The first column row pairs a tall heading with an indented right line
    # of normal size. Only parts of the same size count as one tilted row,
    # so the row is measured from the heading's centre and stays in the body.
    left = [box("L0", 200, 400, 700, height=80)] + [
        box(f"L{i}", 200, 500 + (i - 1) * STEP, 1180) for i in range(1, 20)]
    right = [box("R0", 1500, 410, 2280)] + [
        box(f"R{i}", 1300, 500 + (i - 1) * STEP, 2280) for i in range(1, 20)]
    ordered = texts(order_lines(as_recognized(left + right), W, H))
    assert ordered == names("L", 20) + names("R", 20)


def test_left_aligned_running_footer_is_read_last():
    # A running footer at the left column's text edge, as wide as a third of
    # the column, has no hanging footnote numeral: it stays in the footer.
    lines = column("L", LEFT, 1400, 30) + column("R", RIGHT, 1400, 30)
    for footer in ([box("Kurs Strafrecht AT", 200, 3350, 800)],
                   [box("Copyright Verlag", 200, 3300, 700), box("Kurs 2026", 200, 3350, 500)]):
        ordered = order_lines(as_recognized(lines + footer), W, H)
        assert texts(ordered) == names("L", 30) + names("R", 30) + texts(footer)


def test_one_sided_footer_lines_away_from_the_column_edge_are_read_last():
    # A right-aligned footer part and a mark in the right margin do not
    # start at their column's text edge; a page number at the edge is no
    # line of text.
    lines = column("L", LEFT, 1400, 30) + column("R", RIGHT, 1400, 30)
    for footer in ([box("Titel, Seite 7", 1800, 3300, 2280)],
                   [box("I", 2390, 3400, 2440, height=70)],
                   [box("12", 200, 3300, 260)],
                   [box("12", 1300, 3300, 1360)]):
        ordered = order_lines(as_recognized(lines + footer), W, H)
        assert texts(ordered) == names("L", 30) + names("R", 30) + texts(footer)


def test_running_footer_with_a_right_aligned_part_is_read_last():
    lines = column("L", LEFT, 1400, 30) + column("R", RIGHT, 1400, 30)
    footer = [box("Kurs", 200, 3300, 600), box("Titel, Seite 7", 1800, 3300, 2280)]
    ordered = order_lines(as_recognized(lines + footer), W, H)
    assert texts(ordered) == names("L", 30) + names("R", 30) + ["Kurs", "Titel, Seite 7"]


def test_footnotes_stay_at_the_bottom_of_their_column():
    left = column("L", LEFT, 400, 15) + [box(f"FL{i}", 200, 1400 + i * 45, 1180, height=30)
                                          for i in range(3)]
    right = column("R", RIGHT, 400, 18) + [box(f"FR{i}", 1300, 1540 + i * 45, 2280, height=30)
                                           for i in range(2)]
    ordered = order_lines(as_recognized(left + right), W, H)
    assert texts(ordered) == names("L", 15) + names("FL", 3) + names("R", 18) + names("FR", 2)


def test_full_width_footnotes_follow_both_columns():
    lines = column("L", LEFT, 400, 20) + column("R", RIGHT, 400, 20)
    notes = [box(f"F{i}", 200, 1700 + i * 45, 2280, height=30) for i in range(3)]
    ordered = order_lines(as_recognized(lines + notes), W, H)
    assert texts(ordered) == names("L", 20) + names("R", 20) + names("F", 3)


def test_margin_notes_follow_the_body_of_a_single_column():
    body = column("z", (400, 2200), 400, 15)
    notes = [box("links", 60, 580, 300), box("rechts", 2300, 1000, 2460)]
    ordered = order_lines(as_recognized(body + notes), W, H)
    assert texts(ordered) == names("z", 15) + ["links", "rechts"]


def test_margin_note_of_the_left_column_comes_before_the_right_column():
    lines = column("L", (300, 1180), 400, 20) + column("R", RIGHT, 400, 20)
    note = box("Rand", 40, 400 + 5 * STEP, 200)
    ordered = order_lines(as_recognized(lines + [note]), W, H)
    assert texts(ordered) == names("L", 20) + ["Rand"] + names("R", 20)


def test_note_written_into_the_gutter_does_not_break_the_columns():
    lines = column("L", LEFT, 400, 20) + column("R", RIGHT, 400, 20)
    note = box("Glosse", 1120, 400 + 8 * STEP, 1330)
    ordered = order_lines(as_recognized(lines + [note]), W, H)
    assert texts(ordered) == names("L", 9) + ["Glosse"] + [f"L{i}" for i in range(9, 20)] + names(
        "R", 20
    )


def test_hanging_numerals_stay_in_their_row():
    lines = []
    for i in range(10):
        y = 400 + i * STEP
        lines += [box(f"{i}.", 200, y + 4, 260), box(f"Text{i}", 300, y, 2280)]
    expected = [text for i in range(10) for text in (f"{i}.", f"Text{i}")]
    assert texts(order_lines(as_recognized(lines), W, H)) == expected


def test_short_paragraph_ends_do_not_make_two_columns():
    lines = []
    for i in range(30):
        right = 1000 if i % 4 == 3 else 2280
        lines.append(box(f"z{i}", 200, 400 + i * STEP, right))
    lines.append(box("rechtsbuendig", 1500, 400 + 30 * STEP, 2280))
    ordered = order_lines(as_recognized(lines), W, H)
    assert texts(ordered) == names("z", 30) + ["rechtsbuendig"]


def test_skewed_page_is_read_like_the_upright_page():
    top = column("La", LEFT, 700, 10) + column("Ra", RIGHT, 700, 10)
    heading = box("Zwischentitel", 800, 1340, 1680, height=50)
    bottom = column("Lb", LEFT, 1460, 10) + column("Rb", RIGHT, 1460, 10)
    upright = top + [heading] + bottom
    expected = texts(order_lines(as_recognized(upright), W, H))
    for degrees in (3, -3):
        assert texts(order_lines(as_recognized(rotated(upright, degrees)), W, H)) == expected


def sloped(text, x0, y0, x1, slope, height=LINE):
    """A line starting at (x0, y0) whose right end moves by `slope` per pixel."""
    rise = slope * (x1 - x0)
    return TextLine(
        text, ((x0, y0), (x1, y0 + rise), (x1, y0 + rise + height), (x0, y0 + height)), 0.9
    )


def bowed_column(prefix, x0, top, count, slope):
    return [sloped(f"{prefix}{i}", x0, top + i * STEP, 2280, slope) for i in range(count)]


def test_bowed_right_column_beside_a_straight_left_column_keeps_its_gutter():
    # Scanned near the spine: the right column's lines rise while both column
    # edges stay upright. The median slope of all lines is no page rotation;
    # turning by it tilts the left column into the narrow gutter.
    lines = column("L", (200, 1160), 400, 20) + bowed_column("R", 1212, 400, 20, -0.08)
    assert texts(order_lines(as_recognized(lines), W, H)) == names("L", 20) + names("R", 20)


def test_output_is_a_permutation_with_unplaceable_lines_last():
    lines = column("L", LEFT, 400, 10) + column("R", RIGHT, 400, 10)
    empty = box("   ", 200, 2000, 900)
    broken = TextLine("kaputt", (), 0.5)
    outside = box("draussen", 3000, 4000, 3500)
    recognized = as_recognized(lines)
    recognized[3:3] = [broken, empty]
    recognized.append(outside)
    ordered = order_lines(recognized, W, H)
    assert Counter(map(id, ordered)) == Counter(map(id, recognized))
    assert ordered[-3:] == [broken, empty, outside]


def test_arbitrary_pages_always_yield_a_permutation():
    import random

    rng = random.Random(69)
    for _ in range(300):
        lines = []
        for i in range(rng.randrange(0, 60)):
            x0, y0 = rng.uniform(-100, W), rng.uniform(-100, H)
            x1, y1 = x0 + rng.choice([0, 1, rng.uniform(1, W)]), y0 + rng.choice([0, 1, 40, 300])
            polygon = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
            lines.append(TextLine(rng.choice(["", " ", f"w{i}"]),
                                  rotated([TextLine("", polygon, 0)], rng.uniform(-15, 15))[0]
                                  .polygon, 0.5))
        ordered = order_lines(lines, W, H)
        assert Counter(map(id, ordered)) == Counter(map(id, lines))


def test_fewer_than_two_placeable_lines_keep_their_order():
    lines = [TextLine("kaputt", (), 0.5), box("allein", 200, 400, 900)]
    assert order_lines(lines, W, H) == lines
    assert order_lines([], W, H) == []

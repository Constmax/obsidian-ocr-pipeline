"""Footnote blocks of two columns side by side (Issue #14).

Hemmer solutions set each column's footnotes under that column. The column
split_columns() gives each recognized line reaches the footnote handling, so
the blocks stay apart and a number never takes text from the other column.

  python3 -m pytest pdf2md/test/test_footnote_columns.py
"""
import page_cache
from assembly import AssemblyContext, RecognizedLine, assemble_paragraphs
from conversion import BlockContext, PageMeta, page_block
from layout import split_columns


def z(text, x0, y0, x1, height=9, column=None):
    return RecognizedLine(text, (x0, y0, x1, y0 + height), column)


def two_column_page():
    """Body of both columns, then each column's footnote block below it.

    Laid out like the text layer of `2135_Lösung/p003` (geometry only, the
    text is invented): numbers out-dented at x 95 and 523, the left body
    ending mid-sentence, a centred footer across the gutter, and one
    justified line split into word spans.
    """
    left = [z("Der erste Satz der linken Spalte endet hier nicht", 128, 124, 480),
            z("sofort.1 Ein zweiter Satz folgt und bleibt in der", 128, 136, 480),
            z("linken Spalte, bis er mit ihr endet und dann", 128, 148, 480)]
    for k in range(40):
        left.append(z(f"Weitere Zeile {k} der linken Spalte im Blocksatz", 128,
                      160 + 12 * k, 480))
    left += [z("gesetzte", 128, 640, 238), z("Wörter", 250, 640, 310),
             z("einer", 322, 640, 360), z("Blocksatzzeile", 371, 640, 480),
             z("weitergeht.2 Das gilt auch hier.", 128, 652, 400),
             z("1", 95, 896, 109), z("Muster, Lehrbuch, 2020, Rn. 1.", 128, 896, 479),
             z("2", 95, 908, 109), z("Beispiel, Zeitschrift 2019, 10 (11); Probe,", 128, 908, 480),
             z("Zeitschrift 2018, 20 (21).", 128, 917, 300)]
    right = [z("Die rechte Spalte beginnt mit einem Satz.3", 557, 124, 909)]
    for k in range(40):
        right.append(z(f"Weitere Zeile {k} der rechten Spalte im Blocksatz", 557,
                       136 + 12 * k, 909))
    right += [z("3", 523, 896, 545), z("Autor, Kommentar, Rn. 3; Autorin,", 557, 896, 908),
              z("Zeitschrift 2017, 30 (31).", 557, 906, 850)]
    footer = [z("Kursanbieter - 01/2026", 373, 965, 665)]
    # Text-layer order: row by row across both columns.
    return sorted(left + right + footer, key=lambda line: (line.box[1], line.box[0]))


def assemble(lines):
    return assemble_paragraphs(split_columns(lines)).paragraphs


def test_gutter_found_despite_word_spans_and_centred_footer():
    by_text = {line.text: line.column for line in split_columns(two_column_page())}
    assert by_text["Wörter"] == by_text["Blocksatzzeile"] == 0
    assert by_text["2"] == by_text["1"] == 0
    assert by_text["3"] == by_text["Zeitschrift 2017, 30 (31)."] == 1


def test_word_span_after_a_wide_space_keeps_its_column():
    """A justified line's last word set far from the rest: the span test
    misses it, but it ends before the gutter, so it stays left."""
    page = two_column_page() + [z("Wort", 470, 664, 480)]
    assert {line.text: line.column
            for line in split_columns(page)}["Wort"] == 0


def test_each_definition_keeps_its_own_column():
    paragraphs = assemble(two_column_page())
    defs = [p for p in paragraphs if p.startswith("[^")]
    assert defs == [
        "[^1]: Muster, Lehrbuch, 2020, Rn. 1.",
        "[^2]: Beispiel, Zeitschrift 2019, 10 (11); Probe, Zeitschrift 2018, 20 (21).",
        "[^3]: Autor, Kommentar, Rn. 3; Autorin, Zeitschrift 2017, 30 (31).",
    ]


def test_running_text_continues_past_the_left_footnote_block():
    """The right column's first line goes on with the left body, not with
    the last footnote of the left column."""
    left = [z(f"Zeile {k} links, voll gesetzt im Blocksatz bis zum Rand", 128,
              124 + 12 * k, 480) for k in range(10)]
    left += [z("Deshalb bleibt der Satz der linken Spalte", 128, 244, 480),
             z("4", 95, 900, 109), z("Muster, Lehrbuch, Rn. 4.", 128, 900, 400)]
    right = [z("offen.4 Das folgt aus dem Gesetz.", 557, 124, 909)]
    right += [z(f"Zeile {k} rechts, voll gesetzt im Blocksatz bis zum Rand", 557,
                136 + 12 * k, 909) for k in range(10)]
    paragraphs = assemble(left + right)
    body = [p for p in paragraphs if "offen" in p]
    assert len(body) == 1
    assert "der linken Spalte offen.[^4] Das folgt" in body[0]
    assert "[^4]: Muster, Lehrbuch, Rn. 4." in paragraphs


def two_columns(left, left_notes, right, right_notes, known=True):
    """Each column's body from the top and its footnote block at the foot,
    left column first; `known=False` leaves every column unknown."""
    def column(body, notes, x0, x1, index):
        index = index if known else None
        return (stack(body, 100, x0, x1, index)
                + stack(notes, 900, x0, x1, index, step=12))
    return column(left, left_notes, 128, 480, 0) \
        + column(right, right_notes, 557, 909, 1)


def stack(texts, y, x0, x1, column, step=18):
    return [z(text, x0, y + step * i, x1, column=column)
            for i, text in enumerate(texts)]


def page(lines):
    return page_block(lines, BlockContext(assembly=AssemblyContext()),
                      PageMeta(number=1, source="textlayer")).paragraphs


BODY = [f"Zeile {k} im Blocksatz bis zum Rand." for k in range(3)]


def test_same_number_in_both_columns_the_citing_column_decides():
    """A citation page number after a sentence end ("S. 7. 5 Vgl. ...") looks
    like definition 5 in the right column. The left column cites 5 and holds
    its definition, so that one wins; the right column keeps its text."""
    columns = ([*BODY, "Die Klage ist zulässig.5 Sie ist auch begründet."],
               ["5 Vgl. Muster, Lehrbuch, Rn. 5."],
               [*BODY, "Der Bescheid ist rechtswidrig.6"],
               ["6 Beispiel, Zeitschrift 2016, 7. 5 Vgl. auch Probe 2015."])
    out = page(two_columns(*columns))
    assert "[^5]: Vgl. Muster, Lehrbuch, Rn. 5." in out
    assert "[^6]: Beispiel, Zeitschrift 2016, 7. 5 Vgl. auch Probe 2015." in out
    # Unknown columns fall back to reading order, but lose no digit.
    merged = page(two_columns(*columns, known=False))
    assert ("[^5]: Vgl. Muster, Lehrbuch, Rn. 5. 5 Vgl. auch Probe 2015."
            in merged)


def test_same_number_in_both_columns_right_column_owns_it():
    """Reading order alone would give the left column's stray 7 the number."""
    out = page(two_columns(
        [*BODY, "Das ergibt sich aus der Rechtsprechung.6"],
        ["6 Beispiel, Zeitschrift 2012, S. 3. 7 Vgl. Probe 2015."],
        [*BODY, "Die Behörde hat ihr Ermessen nicht ausgeübt.7"],
        ["7 Muster, Lehrbuch, Rn. 12."]))
    assert "[^7]: Muster, Lehrbuch, Rn. 12." in out


def test_stray_opening_its_column_goes_under_no_other_number():
    """A stray 5 that opens the right column's block has no definition of
    its own column before it; it stays a paragraph instead of joining the
    left column's 5."""
    out = page(two_columns(
        [*BODY, "Die Klage ist zulässig.5"],
        ["5 Muster, Lehrbuch, Rn. 5."],
        [*BODY, "Sie ist auch begründet.6"],
        ["5 Vgl. auch Probe 2015.", "6 Beispiel, Zeitschrift 2016, 7."]))
    assert "[^5]: Muster, Lehrbuch, Rn. 5." in out
    assert "5 Vgl. auch Probe 2015." in out
    assert "[^6]: Beispiel, Zeitschrift 2016, 7." in out


def test_no_citing_column_the_neighbour_number_decides():
    """Neither column cites 2: its mark sits in a full-width line. The left
    column's block is held back until after the right column's, so the
    right column's stray comes first in reading order; the left column
    holds 1, so its 2 is the definition."""
    lines = [z("Ein Satz über die ganze Breite.2", 128, 40, 909),
             *two_columns(BODY, ["1 Muster, Lehrbuch, Rn. 1.",
                                 "2 Beispiel, Zeitschrift 2016, 7."],
                          BODY, ["2 Vgl. Probe 2015."])]
    out = page(lines)
    assert "[^2]: Beispiel, Zeitschrift 2016, 7." in out
    assert "2 Vgl. Probe 2015." in out


def test_paragraph_across_the_gutter_cites_for_both_columns():
    """A left paragraph picked up again in the right column covers both, so
    its mark counts for the right column's definition too; the neighbour
    number then gives 4 to the right column."""
    out = page(two_columns(
        [*BODY, "Der Satz beginnt links und"],
        ["2 Muster, Lehrbuch, Rn. 2. 4 Vgl. Probe 2015."],
        ["endet rechts.4", *BODY],
        ["4 Beispiel, Zeitschrift 2016, 7.", "5 Autor, Kommentar, Rn. 5."]))
    assert "[^4]: Beispiel, Zeitschrift 2016, 7." in out
    assert "[^2]: Muster, Lehrbuch, Rn. 2. 4 Vgl. Probe 2015." in out


def test_cache_keeps_columns(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"pdf contents")
    context = page_cache.build_context(pdf, {"dpi": 150})
    directory = page_cache.cache_directory(tmp_path / "out", pdf)
    lines = [z("a", 0, 0, 400, column=0), z("b", 520, 0, 900, column=1)]
    page_cache.write_page(directory, context, {
        "number": 1, "source": "ocr", "characters": 0, "layout": "zweispaltig",
        "mode": "senkrecht @50%", "lines": lines, "trace": []})
    entry = page_cache.read_page(directory, 1, page_cache.page_key(context, 1))
    assert page_cache.recognized_lines(entry) == lines

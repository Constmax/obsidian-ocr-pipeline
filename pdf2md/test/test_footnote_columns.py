"""Footnote blocks of two columns side by side (Issue #14).

Hemmer solutions set each column's footnotes under that column. The column
split_columns() gives each recognized line reaches the footnote handling, so
the blocks stay apart and a number never takes text from the other column.

  python3 -m pytest pdf2md/test/test_footnote_columns.py
"""
from dataclasses import replace

import page_cache
from assembly import (RecognizedLine, assemble_paragraphs,
                      _attach_footnote_numbers, _footnotes_obsidian)
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


def test_same_number_in_both_columns_the_citing_column_decides():
    """A citation page number after a sentence end ("S. 7. 5 Vgl. ...") looks
    like definition 5 in the right column. The left column cites 5 and holds
    its definition, so that one wins; the right column keeps its text."""
    paragraphs = ["Die Klage ist zulässig.5 Sie ist auch begründet.",
                  "5 Vgl. Muster, Lehrbuch, Rn. 5.",
                  "Der Bescheid ist rechtswidrig.6",
                  "6 Beispiel, Zeitschrift 2016, 7. 5 Vgl. auch Probe 2015."]
    out = _footnotes_obsidian(paragraphs, [{0}, {0}, {1}, {1}])
    assert "[^5]: Vgl. Muster, Lehrbuch, Rn. 5." in out
    assert "[^6]: Beispiel, Zeitschrift 2016, 7. 5 Vgl. auch Probe 2015." in out
    # Unknown columns fall back to reading order, but lose no digit.
    merged = _footnotes_obsidian(paragraphs)
    assert ("[^5]: Vgl. Muster, Lehrbuch, Rn. 5. 5 Vgl. auch Probe 2015."
            in merged)


def test_same_number_in_both_columns_right_column_owns_it():
    """Reading order alone would give the left column's stray 7 the number."""
    paragraphs = ["Das ergibt sich aus der Rechtsprechung.6",
                  "6 Beispiel, Zeitschrift 2012, 7 (9).",
                  "Die Behörde hat ihr Ermessen nicht ausgeübt.7",
                  "7 Muster, Lehrbuch, Rn. 12."]
    out = _footnotes_obsidian(paragraphs, [{0}, {0}, {1}, {1}])
    assert out[-2:] == ["[^6]: Beispiel, Zeitschrift 2012, 7 (9).",
                        "[^7]: Muster, Lehrbuch, Rn. 12."]


def test_stray_opening_its_column_goes_under_no_other_number():
    """A stray 5 that opens the right column's block has no definition of
    its own column before it; it stays a paragraph instead of joining the
    left column's 5."""
    paragraphs = ["Die Klage ist zulässig.5",
                  "5 Muster, Lehrbuch, Rn. 5.",
                  "Sie ist auch begründet.6",
                  "5 Vgl. auch Probe 2015.",
                  "6 Beispiel, Zeitschrift 2016, 7."]
    out = _footnotes_obsidian(paragraphs, [{0}, {0}, {1}, {1}, {1}])
    assert "[^5]: Muster, Lehrbuch, Rn. 5." in out
    assert "5 Vgl. auch Probe 2015." in out
    assert "[^6]: Beispiel, Zeitschrift 2016, 7." in out


def test_no_citing_column_the_neighbour_number_decides():
    """Neither column cites 2 (its mark sits in a full-width line). The
    right column's stray comes first in reading order, but the left column
    holds 1, so its 2 is the definition."""
    paragraphs = ["Ein Satz über die ganze Breite.2",
                  "2 Vgl. Probe 2015.",
                  "1 Muster, Lehrbuch, Rn. 1.",
                  "2 Beispiel, Zeitschrift 2016, 7."]
    out = _footnotes_obsidian(paragraphs, [set(), {1}, {0}, {0}])
    assert "[^2]: Beispiel, Zeitschrift 2016, 7." in out
    assert "2 Vgl. Probe 2015." in out


def test_paragraph_across_the_gutter_cites_for_both_columns():
    """A left paragraph picked up again in the right column covers both, so
    its mark counts for the right column's definition too; the neighbour
    number then gives 4 to the right column."""
    paragraphs = ["Der Satz beginnt links und endet rechts.4",
                  "2 Muster, Lehrbuch, Rn. 2. 4 Vgl. Probe 2015.",
                  "4 Beispiel, Zeitschrift 2016, 7.",
                  "5 Autor, Kommentar, Rn. 5."]
    out = _footnotes_obsidian(paragraphs, [{0, 1}, {0}, {1}, {1}])
    assert "[^4]: Beispiel, Zeitschrift 2016, 7." in out
    assert "[^2]: Muster, Lehrbuch, Rn. 2. 4 Vgl. Probe 2015." in out


def test_number_is_not_attached_to_text_of_the_other_column():
    number = z("4", 95, 930, 109, column=0)
    other = z("Muster, Lehrbuch, Rn. 12.", 557, 930, 900, column=1)
    assert _attach_footnote_numbers([number, other]) == [number, other]
    same = replace(other, box=(128, 930, 480, 939), column=0)
    assert _attach_footnote_numbers([number, same])[0].text \
        == "4 Muster, Lehrbuch, Rn. 12."


def test_number_sorted_after_its_text_on_the_same_row():
    """A number set 2 thousandths lower than its text sorts after it."""
    lines = [z("Muster, Lehrbuch, Rn. 82.", 557, 917, 758),
             z("Muster, Lehrbuch, Rn. 84f.", 557, 927, 908),
             z("12", 523, 929, 545)]
    out = _attach_footnote_numbers(lines)
    assert [line.text for line in out] == ["Muster, Lehrbuch, Rn. 82.",
                                         "12 Muster, Lehrbuch, Rn. 84f."]


def test_page_number_beside_the_running_footer_stays_apart():
    """A page number on the footer's row is no footnote number: joined, the
    footer would no longer be recognized as a running line."""
    lines = [z("Autorin A, Kursanbieter - 01/2026", 651, 948, 832),
             z("**1**", 445, 949, 454)]
    assert _attach_footnote_numbers(lines) == lines


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

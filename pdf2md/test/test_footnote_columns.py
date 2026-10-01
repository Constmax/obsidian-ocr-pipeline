"""Footnote blocks of two columns side by side (Issue #14).

Hemmer solutions set each column's footnotes under that column. The column
assignment of split_columns_indexed() reaches the footnote handling, so the
blocks stay apart and a number never takes text from the other column.

  python3 -m pytest pdf2md/test/test_footnote_columns.py
"""
import json

import page_cache
from assembly import (assemble_paragraphs, attach_footnote_numbers,
                      footnotes_obsidian)
from layout import split_columns, split_columns_indexed


def z(text, x0, y0, x1, height=9):
    return [text, (x0, y0, x1, y0 + height)]


def two_column_page():
    """Body of both columns, then each column's footnote block below it.

    Laid out like the text layer of `2135_Lösung` S. 3: numbers out-dented
    at x 95 and 523, the left body ending mid-sentence, a centred footer
    across the gutter, and one justified line split into word spans.
    """
    left = [z("Die Anordnung der sofortigen Vollziehung ist kein", 128, 124, 480),
            z("Verwaltungsakt.1 Sie teilt das Schicksal des Bescheids,", 128, 136, 480),
            z("der ihr zugrunde liegt, und ist daher mit ihm", 128, 148, 480)]
    for k in range(40):
        left.append(z(f"Weitere Zeile {k} der linken Spalte im Blocksatz", 128,
                      160 + 12 * k, 480))
    left += [z("aufschiebenden", 128, 640, 238), z("Wirkung", 250, 640, 310),
             z("nicht", 322, 640, 360), z("bestandskräftig", 371, 640, 480),
             z("zu betrachten.2 Das gilt auch hier.", 128, 652, 400),
             z("1", 95, 896, 109), z("OVG Bremen, DVBl. 1980, 420 (422).", 128, 896, 479),
             z("2", 95, 908, 109), z("OVG Koblenz, NVwZ 1988, 478; VGH Mannheim,", 128, 908, 480),
             z("NVwZ 1995, 292 (293).", 128, 917, 300)]
    right = [z("angefochten werden. Die Begründung genügt nicht.3", 557, 124, 909)]
    for k in range(40):
        right.append(z(f"Weitere Zeile {k} der rechten Spalte im Blocksatz", 557,
                       136 + 12 * k, 909))
    right += [z("3", 523, 896, 545), z("Kopp/Schenke, VwGO, Rn. 82; Rammelt/Schulz,", 557, 896, 908),
              z("Jura 2019, 1207 (1210).", 557, 906, 850)]
    footer = [z("Hemmer/Wüst - 04/2026", 373, 965, 665)]
    # Text-layer order: row by row across both columns.
    return sorted(left + right + footer, key=lambda line: (line[1][1], line[1][0]))


def assemble(lines):
    ordered, columns = split_columns_indexed(lines)
    return assemble_paragraphs(ordered, columns=columns).paragraphs


def test_gutter_found_despite_word_spans_and_centred_footer():
    ordered, columns = split_columns_indexed(two_column_page())
    by_text = {line[0]: column for line, column in zip(ordered, columns)}
    assert by_text["Wirkung"] == by_text["bestandskräftig"] == 0
    assert by_text["2"] == by_text["1"] == 0
    assert by_text["3"] == by_text["Jura 2019, 1207 (1210)."] == 1
    # split_columns() keeps its plain list and the same order.
    assert split_columns(two_column_page()) == ordered


def test_each_definition_keeps_its_own_column():
    paragraphs = assemble(two_column_page())
    defs = [p for p in paragraphs if p.startswith("[^")]
    assert defs == [
        "[^1]: OVG Bremen, DVBl. 1980, 420 (422).",
        "[^2]: OVG Koblenz, NVwZ 1988, 478; VGH Mannheim, NVwZ 1995, 292 (293).",
        "[^3]: Kopp/Schenke, VwGO, Rn. 82; Rammelt/Schulz, Jura 2019, 1207 (1210).",
    ]


def test_running_text_continues_past_the_left_footnote_block():
    """The right column's first line goes on with the left body, not with
    the last footnote of the left column."""
    left = [z(f"Zeile {k} links, voll gesetzt im Blocksatz bis zum Rand", 128,
              124 + 12 * k, 480) for k in range(10)]
    left += [z("Deshalb ist die Anordnung nicht selbstständig", 128, 244, 480),
             z("4", 95, 900, 109), z("Kopp/Schenke, VwGO, Rn. 78.", 128, 900, 400)]
    right = [z("anfechtbar.4 Das folgt aus dem Gesetz.", 557, 124, 909)]
    right += [z(f"Zeile {k} rechts, voll gesetzt im Blocksatz bis zum Rand", 557,
                136 + 12 * k, 909) for k in range(10)]
    paragraphs = assemble(left + right)
    body = [p for p in paragraphs if "anfechtbar" in p]
    assert len(body) == 1
    assert "nicht selbstständig anfechtbar.[^4] Das folgt" in body[0]
    assert "[^4]: Kopp/Schenke, VwGO, Rn. 78." in paragraphs


def test_same_number_in_both_columns_the_citing_column_decides():
    """A citation page number after a sentence end ("S. 7. 5 Vgl. ...") looks
    like definition 5 in the right column. The left column cites 5 and holds
    its definition, so that one wins; the right column keeps its text."""
    paragraphs = ["Die Klage ist zulässig.5 Sie ist auch begründet.",
                  "5 Vgl. BGH, NJW 2014, 1524.",
                  "Der Bescheid ist rechtswidrig.6",
                  "6 Stellhorn, BayVBl. 2016, 7. 5 Vgl. auch Jura 2015."]
    out = footnotes_obsidian(paragraphs, [0, 0, 1, 1])
    assert "[^5]: Vgl. BGH, NJW 2014, 1524." in out
    assert "[^6]: Stellhorn, BayVBl. 2016, 7. 5 Vgl. auch Jura 2015." in out
    # Unknown columns fall back to reading order, but lose no digit.
    merged = footnotes_obsidian(paragraphs)
    assert ("[^5]: Vgl. BGH, NJW 2014, 1524. 5 Vgl. auch Jura 2015."
            in merged)


def test_same_number_in_both_columns_right_column_owns_it():
    """Reading order alone would give the left column's stray 7 the number."""
    paragraphs = ["Das ergibt sich aus der Rechtsprechung.6",
                  "6 BVerwG, NVwZ 2012, 7 (9).",
                  "Die Behörde hat ihr Ermessen nicht ausgeübt.7",
                  "7 Kopp/Schenke, VwGO, Rn. 12."]
    out = footnotes_obsidian(paragraphs, [0, 0, 1, 1])
    assert out[-2:] == ["[^6]: BVerwG, NVwZ 2012, 7 (9).",
                        "[^7]: Kopp/Schenke, VwGO, Rn. 12."]


def test_number_is_not_attached_to_text_of_the_other_column():
    number = z("4", 95, 930, 109) + [None, 0]
    other = z("Kopp/Schenke, VwGO, Rn. 12.", 557, 930, 900) + [None, 1]
    assert attach_footnote_numbers([number, other]) == [number, other]
    same = z("Kopp/Schenke, VwGO, Rn. 12.", 128, 930, 480) + [None, 0]
    assert attach_footnote_numbers([number, same])[0][0] \
        == "4 Kopp/Schenke, VwGO, Rn. 12."


def test_number_sorted_after_its_text_on_the_same_row():
    """A number set 2 thousandths lower than its text sorts after it."""
    lines = [z("Kopp/Schenke, VwGO, Rn. 82.", 557, 917, 758),
             z("Kopp/Schenke, VwGO, Rn. 84f.", 557, 927, 908),
             z("12", 523, 929, 545)]
    out = attach_footnote_numbers(lines)
    assert [line[0] for line in out] == ["Kopp/Schenke, VwGO, Rn. 82.",
                                         "12 Kopp/Schenke, VwGO, Rn. 84f."]


def test_page_number_beside_the_running_footer_stays_apart():
    """A page number on the footer's row is no footnote number: joined, the
    footer would no longer be recognized as a running line."""
    lines = [z("RA Dr. Michael Hein, M.A., LL.M. - 05/2026", 651, 948, 832),
             z("**1**", 445, 949, 454)]
    assert attach_footnote_numbers(lines) == lines


def test_cache_keeps_columns_and_rejects_a_mismatch(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"pdf contents")
    context = page_cache.build_context(pdf, {"dpi": 150})
    directory = page_cache.cache_directory(tmp_path / "out", pdf)
    page = {"number": 1, "source": "ocr", "characters": 0, "layout": "zweispaltig",
            "mode": "senkrecht @50%", "lines": [["a", [0, 0, 400, 10]],
                                                ["b", [520, 0, 900, 10]]],
            "trace": [], "columns": [0, 1]}
    page_cache.write_page(directory, context, page)
    key = page_cache.page_key(context, 1)
    assert page_cache.read_page(directory, 1, key)["columns"] == [0, 1]

    path = page_cache.page_path(directory, 1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["page"]["columns"] = [0]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert page_cache.read_page(directory, 1, key) is None

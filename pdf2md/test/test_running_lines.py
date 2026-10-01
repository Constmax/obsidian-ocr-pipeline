"""Headers and footers of scanned course pages (Issue #161)."""

import fitz

from assembly import (AssemblyContext, RecognizedLine, assemble_paragraphs,
                      is_boilerplate)
from conversion import assembly_context

AUTHOR = "RA Dr. Erika Muster, M.A., LL.M. - 03/2026"
FOOTER = "k/m/s - 41-Il"
LABEL = "Kursreihe-B-7"
CONTEXT = AssemblyContext(frozenset({AUTHOR, FOOTER}),
                          frozenset({AUTHOR, FOOTER}))
BODY = "Der Anspruch ist entstanden und nicht erloschen."


def _pdf(pages, lines_on):
    """A vector PDF; `lines_on(number)` gives (text, relative y) per page."""
    doc = fitz.open()
    for number in range(1, pages + 1):
        page = doc.new_page(width=600, height=800)
        for text, rel in lines_on(number):
            page.insert_text(fitz.Point(40, rel * 800), text, fontsize=9)
    return doc


def _footer(running_line):
    """A context whose only running line is a footer."""
    lines = frozenset({running_line})
    return AssemblyContext(lines, lines)


def _misread_footer(text, context=CONTEXT, y=960):
    return is_boilerplate(text, y, context=context, ocr_page=True)


def test_a_course_label_below_the_header_zone_is_a_running_line():
    with _pdf(4, lambda _n: [(LABEL, 0.115)]) as doc:
        assert LABEL in assembly_context(doc).running_lines


def test_a_line_below_the_header_zone_counts_only_on_most_pages():
    def lines_on(number):
        return [("Beispiel:", 0.115)] if number in (2, 5) else []

    with _pdf(6, lines_on) as doc:
        assert "Beispiel:" not in assembly_context(doc).running_lines


def test_a_label_on_two_thirds_of_the_pages_is_a_running_line():
    def lines_on(number):
        return [(LABEL, 0.115)] if number <= 6 else []

    with _pdf(9, lines_on) as doc:
        assert LABEL in assembly_context(doc).running_lines


def test_a_short_document_needs_the_label_on_three_pages():
    with _pdf(2, lambda _n: [(LABEL, 0.115)]) as doc:
        assert LABEL not in assembly_context(doc).running_lines


def test_the_header_zone_still_needs_two_pages():
    def lines_on(number):
        return [("Klausurenkurs/Hessen", 0.06)] if number in (2, 5) else []

    with _pdf(6, lines_on) as doc:
        assert "Klausurenkurs/Hessen" in assembly_context(doc).running_lines


def test_only_running_lines_of_the_footer_zone_are_footer_lines():
    with _pdf(3, lambda _n: [(LABEL, 0.115), (AUTHOR, 0.96)]) as doc:
        context = assembly_context(doc)
    assert context.running_lines == {LABEL, AUTHOR}
    assert context.footer_lines == {AUTHOR}


def test_a_text_layer_running_line_matches_without_its_asterisk():
    """Running lines and recognized lines are normalised alike."""
    with _pdf(2, lambda _n: [("Skript Teil 1*", 0.05)]) as doc:
        context = assembly_context(doc)
    result = assemble_paragraphs(
        [RecognizedLine("Skript Teil 1*", (40, 40, 300, 50)),
         RecognizedLine(BODY, (40, 300, 500, 312))], context)
    assert result.paragraphs == [BODY]


def test_both_ends_of_a_footer_cut_at_the_gutter_are_dropped():
    lines = [
        RecognizedLine(BODY, (100, 500, 480, 512)),
        RecognizedLine("RA Dr. Erika Muster, M", (690, 955, 998, 966)),
        # The cut runs through "Muster"'s "t"; its right half reads as "l".
        RecognizedLine("ler, M.A., LL.M. - 03/2026", (0, 953, 330, 966)),
        RecognizedLine("/s - 41-11", (0, 948, 106, 957)),
    ]
    result = assemble_paragraphs(lines, CONTEXT, ocr_page=True)
    assert result.paragraphs == [BODY]
    assert [reason for _, reason in result.discarded] == ["running_line"] * 3


def test_a_whole_footer_read_with_ocr_confusions_is_dropped():
    assert _misread_footer("RA Dr. Erika Muster, M.A., Ll.M. - 03/2026", y=950)


def test_a_footer_read_with_a_bar_is_dropped():
    assert _misread_footer("RA Dr. Erika Muster, M.A., L|.M. - 03/2026", y=950)


def test_a_piece_of_a_header_running_line_is_kept():
    """Only a footer is cut at the gutter in the footer band: "Fall 9" there
    may be a heading, not the end of the page header "Muster - Fall 9"."""
    header = AssemblyContext(frozenset({"Muster - Fall 9"}))
    assert not _misread_footer("Fall 9", header)
    assert _misread_footer("Fall 9", _footer("Muster - Fall 9"))


def test_a_word_that_merely_ends_a_running_line_is_kept():
    """A footnote line may end on a word the running line starts with."""
    context = _footer("Musterrecht-AT/Hessen")
    assert not _misread_footer("Musterrecht I", context)
    assert not _misread_footer("Hessen", context)


def test_a_lone_word_of_a_short_running_line_is_kept():
    """40 % of a short running line is a single word."""
    context = AssemblyContext(
        frozenset({"ÖR Hessen", "Musterrecht II – Grundfragen"}),
        frozenset({"ÖR Hessen", "Musterrecht II – Grundfragen"}))
    assert not _misread_footer("Hessen", context)
    assert not _misread_footer("Grundfragen", context)
    assert _misread_footer("Musterrecht II –", context)


def test_a_piece_of_a_running_line_on_a_text_layer_page_is_kept():
    """A text layer is not cut at the gutter: a piece there is page text."""
    assert not is_boilerplate("RA Dr. Erika Muster, M", 955, context=CONTEXT)


def test_a_piece_of_a_running_line_above_the_footer_is_kept():
    assert not _misread_footer("RA Dr. Erika Muster, M", y=500)


def test_a_short_piece_is_kept():
    assert not _misread_footer("/2026")


def test_a_piece_from_the_middle_is_kept():
    assert not _misread_footer("Erika Muster")


def test_a_page_header_with_a_comma_is_boilerplate():
    assert is_boilerplate("Sachverhalte, Seite 2")


def test_a_sentence_citing_a_page_is_kept():
    assert not is_boilerplate("Vgl. die Lösung, Seite 4.")

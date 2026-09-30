"""Headers and footers of scanned course pages (Issue #161)."""

import fitz

from assembly import AssemblyContext, assemble_paragraphs, is_boilerplate
from conversion import running_lines

AUTHOR = "RA Dr. Erika Muster, M.A., LL.M. - 03/2026"
FOOTER = "h/w/t - 26-Il"
CONTEXT = AssemblyContext(frozenset({AUTHOR, FOOTER}), ocr_page=True)
BODY = "Der Anspruch ist entstanden und nicht erloschen."


def _pdf(pages, lines_on):
    """A vector PDF; `lines_on(number)` gives (text, relative y) per page."""
    doc = fitz.open()
    for number in range(1, pages + 1):
        page = doc.new_page(width=600, height=800)
        for text, rel in lines_on(number):
            page.insert_text(fitz.Point(40, rel * 800), text, fontsize=9)
    return doc


def test_a_course_label_below_the_header_zone_is_a_running_line():
    with _pdf(4, lambda _n: [("SchuldR-BT-2", 0.115)]) as doc:
        assert "SchuldR-BT-2" in running_lines(doc)


def test_a_line_below_the_header_zone_counts_only_on_most_pages():
    def lines_on(number):
        return [("Beispiel:", 0.115)] if number in (2, 5) else []

    with _pdf(6, lines_on) as doc:
        assert "Beispiel:" not in running_lines(doc)


def test_a_label_on_two_thirds_of_the_pages_is_a_running_line():
    def lines_on(number):
        return [("SchuldR-BT-2", 0.115)] if number <= 6 else []

    with _pdf(9, lines_on) as doc:
        assert "SchuldR-BT-2" in running_lines(doc)


def test_a_short_document_needs_the_label_on_three_pages():
    with _pdf(2, lambda _n: [("SchuldR-BT-2", 0.115)]) as doc:
        assert "SchuldR-BT-2" not in running_lines(doc)


def test_the_header_zone_still_needs_two_pages():
    def lines_on(number):
        return [("Klausurenkurs/Hessen", 0.06)] if number in (2, 5) else []

    with _pdf(6, lines_on) as doc:
        assert "Klausurenkurs/Hessen" in running_lines(doc)


def test_both_ends_of_a_footer_cut_at_the_gutter_are_dropped():
    lines = [
        [BODY, (100, 500, 480, 512)],
        ["RA Dr. Erika Muster, M", (690, 955, 998, 966)],
        # The cut runs through "Muster"'s "t"; its right half reads as "l".
        ["ler, M.A., LL.M. - 03/2026", (0, 953, 330, 966)],
        ["/t - 26-11", (0, 948, 106, 957)],
    ]
    result = assemble_paragraphs(lines, CONTEXT)
    assert result.paragraphs == [BODY]
    assert len(result.discarded) == 3


def test_a_whole_footer_read_with_ocr_confusions_is_dropped():
    assert is_boilerplate("RA Dr. Erika Muster, M.A., Ll.M. - 03/2026", 950,
                          context=CONTEXT)


def test_a_footer_read_with_a_bar_is_dropped():
    assert is_boilerplate("RA Dr. Erika Muster, M.A., L|.M. - 03/2026", 950,
                          context=CONTEXT)


def test_a_word_that_merely_ends_a_running_line_is_kept():
    """A footnote line may end on a word the running line starts with."""
    context = AssemblyContext(frozenset({"Verwaltungsrecht-AT/Hessen"}),
                              ocr_page=True)
    assert not is_boilerplate("Verwaltungsrecht I", 960, context=context)
    assert not is_boilerplate("Hessen", 960, context=context)


def test_a_lone_word_of_a_short_running_line_is_kept():
    """40 % of a short running line is a single word."""
    context = AssemblyContext(
        frozenset({"ÖR Hessen", "Staatsrecht II – Grundrechte"}), ocr_page=True)
    assert not is_boilerplate("Hessen", 960, context=context)
    assert not is_boilerplate("Grundrechte", 960, context=context)
    assert is_boilerplate("Staatsrecht II –", 960, context=context)


def test_a_piece_of_a_running_line_on_a_text_layer_page_is_kept():
    """A text layer is not cut at the gutter: a piece there is page text."""
    text_layer = AssemblyContext(CONTEXT.running_lines)
    assert not is_boilerplate("RA Dr. Erika Muster, M", 955, context=text_layer)


def test_a_piece_of_a_running_line_above_the_footer_is_kept():
    assert not is_boilerplate("RA Dr. Erika Muster, M", 500, context=CONTEXT)


def test_a_short_piece_is_kept():
    assert not is_boilerplate("/2026", 960, context=CONTEXT)


def test_a_piece_from_the_middle_is_kept():
    assert not is_boilerplate("Erika Muster", 960, context=CONTEXT)


def test_a_page_header_with_a_comma_is_boilerplate():
    assert is_boilerplate("Sachverhalte, Seite 2")


def test_a_sentence_citing_a_page_is_kept():
    assert not is_boilerplate("Vgl. die Lösung, Seite 4.")

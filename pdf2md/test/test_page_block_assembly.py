"""What assembly does to a page, seen at the page-block function (Issue #145).

One test per behavior the golden snapshot used to cover, built from
synthetic recognized lines with invented text. Boxes are in thousandths of
the page; body lines sit 18 apart, so a larger gap starts a paragraph. Page
cases check the same behaviors against real pages.

  python3 -m pytest pdf2md/test/test_page_block_assembly.py
"""
import fitz

from assembly import AssemblyContext, RecognizedLine
from conversion import BlockContext, PageMeta, page_block, textlayer_lines
from layout import split_columns

FOOTER = "Kursanbieter Skript Schuldrecht - 01/2026"
FULL = ("Die erste Zeile eines Absatzes im Blocksatz.",
        "Die zweite Zeile läuft voll bis an den Rand.",
        "Die dritte Zeile läuft voll bis an den Rand.",
        "Die vierte Zeile läuft voll bis an den Rand.")


def z(text, x0=100, y0=None, x1=900, height=14, column=None, container=None):
    box = None if y0 is None else (x0, y0, x1, y0 + height)
    return RecognizedLine(text, box, column, container)


def stack(*texts, y=100, step=18, x0=100, x1=900, column=None):
    """Lines one below the other, at body spacing."""
    return [z(text, x0, y + step * i, x1, column=column)
            for i, text in enumerate(texts)]


def block(lines, source="textlayer", running=()):
    running = frozenset(running)
    context = BlockContext(assembly=AssemblyContext(running, running))
    return page_block(lines, context, PageMeta(number=1, source=source))


def paragraphs(lines, **kwargs):
    return block(lines, **kwargs).paragraphs


def discarded(lines, **kwargs):
    return [(line.text, reason)
            for line, reason in block(lines, **kwargs).discarded]


# --- Footnote numbers and definitions --------------------------------------

def test_outdented_footnote_number_joins_its_text_as_a_definition():
    assert paragraphs([
        *stack("Der Anspruch ist begründet.1 Er ist auch fällig.",
               "Die Einrede greift nicht durch."),
        z("1", 8, 915, 20, height=10),
        z("Vgl. Muster, Lehrbuch, Rn. 7.", 50, 915, 400, height=10),
    ]) == ["Der Anspruch ist begründet.[^1] Er ist auch fällig. "
           "Die Einrede greift nicht durch.", "",
           "[^1]: Vgl. Muster, Lehrbuch, Rn. 7."]


def test_footnote_number_takes_no_text_from_the_other_column():
    """The right column holds only a footnote line, so it follows the left
    column's number in reading order (Issue #14)."""
    left = [*stack(*FULL, x0=128, x1=480, column=0),
            z("4", 95, 900, 109, height=9, column=0)]
    text = "Muster, Lehrbuch, Rn. 12."
    assert paragraphs([*left, z(text, 557, 930, 900, height=9, column=1)])[1:] \
        == ["4", text]
    assert paragraphs([*left, z(text, 128, 930, 480, height=9, column=0)])[-1] \
        == "[^4]: " + text


def test_footnote_number_set_lower_than_its_text_still_joins_it():
    assert paragraphs([
        *stack("Der Satz endet mit einem Zeichen.11", "Mehr Text folgt.12"),
        z("11 Muster, Lehrbuch, Rn. 82.", 557, 917, 758, height=9),
        z("Muster, Lehrbuch, Rn. 84f.", 557, 927, 908, height=9),
        z("12", 523, 929, 545, height=9),
    ]) == ["Der Satz endet mit einem Zeichen.[^11] Mehr Text folgt.[^12]", "",
           "[^11]: Muster, Lehrbuch, Rn. 82.",
           "[^12]: Muster, Lehrbuch, Rn. 84f."]


def test_page_number_beside_the_running_footer_stays_apart():
    """Joined to the page number, the footer would no longer be a running
    line."""
    assert discarded([
        z(FOOTER, 651, 948, 832, height=9),
        z("**1**", 445, 949, 454, height=9),
    ], running={FOOTER}) == [(FOOTER, "running_line"), ("**1**", "page_number")]


def test_footnote_block_splits_into_definitions_but_not_inside_citations():
    assert paragraphs([
        *stack("Der Bescheid ist rechtswidrig.8 Er ist aufzuheben.9",
               "Das gilt auch hier."),
        *stack("8 Gericht; Autor/Autorin, § 35 VwVfG, Rn. 18.",
               "9 Autor/Autorin, Art. 3 III 1 GG.", y=900, step=12),
    ]) == ["Der Bescheid ist rechtswidrig.[^8] Er ist aufzuheben.[^9] "
           "Das gilt auch hier.", "",
           "[^8]: Gericht; Autor/Autorin, § 35 VwVfG, Rn. 18.",
           "[^9]: Autor/Autorin, Art. 3 III 1 GG."]


def test_number_after_a_comma_in_a_citation_starts_no_definition():
    assert paragraphs([
        *stack("Das ist umstritten.6", "Mehr dazu unten."),
        z("6 Autor, Zeitschrift 2016, 77 (78); grundlegend Gericht.", y0=900),
    ]) == ["Das ist umstritten.[^6] Mehr dazu unten.", "",
           "[^6]: Autor, Zeitschrift 2016, 77 (78); grundlegend Gericht."]


def test_duplicate_footnote_number_keeps_its_digits():
    """A number stands once on a page; the second definition stays, digits
    included, so no text is lost (Issue #14)."""
    assert paragraphs([
        *stack("Text.1", "Mehr Text."),
        *stack("1 Erste Quelle.", "1 Zweite Quelle.", y=900, step=12),
    ]) == ["Text.[^1] Mehr Text.", "", "[^1]: Erste Quelle. 1 Zweite Quelle."]


def test_mark_glued_to_an_abbreviation_ending_in_a_roman_letter():
    assert paragraphs([
        *stack("Die Vorlage nach Art. 267 AEUV12 ist zulässig.", "Mehr."),
        z("12 Vgl. Gericht.", y0=900),
    ]) == ["Die Vorlage nach Art. 267 AEUV[^12] ist zulässig. Mehr.", "",
           "[^12]: Vgl. Gericht."]


def test_latex_footnote_marks_become_obsidian_marks():
    assert paragraphs([
        *stack(r"Erster Satz.\(^{2}\) Zweiter Satz.$^{3}$ Dritter.^{4}"),
    ], source="ocr") == ["Erster Satz.[^2] Zweiter Satz.[^3] Dritter.[^4]"]


def test_a_table_gets_no_footnote_marks():
    assert paragraphs([
        *stack("Er ist ein Besitzdiener.1", "Mehr Text."),
        z("| a | 1 |", 100, 200, 900, container="tabelle"),
        *stack("1 Vgl. Muster, Lehrbuch, Rn. 7.",
               "2 So auch Autor, § 855 Rn. 14.", y=900, step=12),
    ]) == ["Er ist ein Besitzdiener.[^1] Mehr Text.", "| a | 1 |", "",
           "[^1]: Vgl. Muster, Lehrbuch, Rn. 7.",
           "[^2]: So auch Autor, § 855 Rn. 14."]


# --- Running lines, page numbers and boilerplate ---------------------------

def test_running_line_is_discarded_with_its_reason():
    body = "Der Anspruch ist entstanden und nicht erloschen."
    page = block([z("Skript **Schuldrecht** AT", y0=20), z(body, y0=200)],
                 running={"Skript Schuldrecht AT"})
    assert page.paragraphs == [body]
    assert [(line.text, reason) for line, reason in page.discarded] == [
        ("Skript **Schuldrecht** AT", "running_line")]


def test_running_line_counts_with_other_spacing_anywhere_on_the_page():
    assert discarded([z("Skript   Schuldrecht  AT", y0=300)],
                     running={"Skript Schuldrecht AT"}) == [
        ("Skript   Schuldrecht  AT", "running_line")]


def test_misread_running_footer_is_discarded_on_an_ocr_page_only():
    misread = "Kursanbieter Skript Schuldrecht - 0l/2026"
    lines = [*stack("Der Anspruch ist entstanden.", "Mehr."),
             z(misread, y0=960)]
    assert discarded(lines, source="ocr", running={FOOTER}) == [
        (misread, "running_line")]
    assert discarded(lines, running={FOOTER}) == []


def test_page_number_is_discarded_at_the_page_edge_only():
    assert discarded([z("543", 480, 950, 520), z("Der Satz.", y0=200)]) == [
        ("543", "page_number")]
    assert discarded([z("543", 480, 500, 520), z("Der Satz.", y0=200)]) == []


def test_provider_lines_are_discarded_as_boilerplate():
    assert discarded([
        z("Juristisches Repetitorium für Recht"),
        z("hemmer"),
        z("– 1 –"),
        z("26-I"),
        z("Mainz - Man"),
        z("Schuldrecht AT – Fall 12 | Skript"),
    ]) == [("Juristisches Repetitorium für Recht", "boilerplate"),
           ("hemmer", "boilerplate"), ("– 1 –", "boilerplate"),
           ("26-I", "boilerplate"), ("Mainz - Man", "boilerplate")]


# --- Headings and enumerations ---------------------------------------------

def test_outline_labels_become_headings_of_their_level():
    assert paragraphs([
        z("**A. Grundsätzliches zum Unterlassen**", x1=500, y0=100),
        z("Zu unterscheiden sind das echte und das unechte Delikt.", y0=130),
        z("II. Exkurs zum Streitstand", x1=400, y0=180),
        z("1. Anspruch aus § 816 I S. 2 BGB", x1=400, y0=230),
        z("a) Erster Unterpunkt", x1=400, y0=280),
        z("aa) Zweite Ebene", x1=400, y0=330),
        z("(1) Dritte Ebene", x1=400, y0=380),
    ]) == ["## A. Grundsätzliches zum Unterlassen",
           "Zu unterscheiden sind das echte und das unechte Delikt.",
           "### II. Exkurs zum Streitstand",
           "#### 1. Anspruch aus § 816 I S. 2 BGB",
           "##### a) Erster Unterpunkt",
           "###### aa) Zweite Ebene",
           "###### (1) Dritte Ebene"]


def test_a_labelled_sentence_keeps_its_text_and_bolds_a_letter_label():
    assert paragraphs([
        z("cc) Diese Ansicht ist mit der h.M. abzulehnen.", y0=100),
        z("3. Die Klage ist zulässig und begründet.", y0=150),
    ]) == ["**cc)** Diese Ansicht ist mit der h.M. abzulehnen.",
           "3. Die Klage ist zulässig und begründet."]


def test_a_bold_labelled_sentence_is_still_a_heading():
    assert paragraphs([
        z("**1. Anspruch aus § 985 BGB auf Rückgabe des Geldes.**", y0=100),
    ]) == ["#### 1. Anspruch aus § 985 BGB auf Rückgabe des Geldes."]


def test_a_bold_label_counts_for_the_level():
    assert paragraphs([z("**b)** Gemäß der herrschenden Meinung")]) == [
        "##### b) Gemäß der herrschenden Meinung"]


def test_an_abbreviation_is_no_outline_label():
    assert paragraphs([z("h. L. und Rechtsprechung stimmen überein.")]) == [
        "h. L. und Rechtsprechung stimmen überein."]


# --- Margin labels ---------------------------------------------------------

def test_margin_label_on_the_body_indent_flows_into_its_sentence():
    assert paragraphs([
        z("**Beispiel:**", 166, 98, 237),
        z("Die Käuferin holt das Rad beim Händler ab und bezahlt dabei "
          "mit einem", 166, 97, 899),
        z("Scheck, der später nicht gedeckt ist.", 166, 116, 899),
    ]) == ["**Beispiel:** Die Käuferin holt das Rad beim Händler ab und "
           "bezahlt dabei mit einem Scheck, der später nicht gedeckt ist."]


def test_outdented_margin_label_moves_to_the_start_of_its_block():
    page = paragraphs([
        z("Der Garant muss den Erfolg abwenden, so dass sein", 200, 100, 900),
        z("**Merke:**", 150, 118, 210),
        z("Unterlassen dem aktiven Tun entspricht, § 13 StGB.", 200, 118, 900),
        z("Daran schließt die Frage der Garantenstellung an.", 200, 136, 900),
        z("Sie ist der Kern jeder Prüfung.", 200, 154, 900),
    ])
    assert page[0].startswith("**Merke:** Der Garant muss")


def test_margin_label_on_the_body_indent_is_not_moved():
    page = paragraphs([
        z("Der Garant muss den Erfolg abwenden.", 200, 100, 900),
        z("**Merke:**", 200, 118, 260),
        z("Die Garantenstellung ist der Kern der Prüfung.", 200, 136, 900),
        z("Sie folgt aus Gesetz, Vertrag oder Ingerenz.", 200, 154, 900),
    ])
    assert page[0] == "Der Garant muss den Erfolg abwenden."
    assert page[1].startswith("**Merke:** Die Garantenstellung")


# --- Short lines -----------------------------------------------------------

def _labelled_line_in_justified_text(label_x1, follower):
    return paragraphs([
        *stack(*FULL),
        z("1. Anspruch aus § 433 BGB", 100, 172, label_x1),
        z(follower, 100, 190, 900),
        *stack(*FULL, y=208),
    ])


def test_a_short_labelled_line_in_justified_text_is_a_heading():
    page = _labelled_line_in_justified_text(400, "Der Vertrag ist wirksam.")
    assert page[1] == "#### 1. Anspruch aus § 433 BGB"
    assert page[2].startswith("Der Vertrag ist wirksam.")


def test_a_full_labelled_line_in_justified_text_runs_on():
    page = _labelled_line_in_justified_text(900, "Der Vertrag ist wirksam.")
    assert page[1].startswith("1. Anspruch aus § 433 BGB Der Vertrag")


def test_a_short_line_followed_by_lowercase_text_runs_on():
    page = _labelled_line_in_justified_text(400, "ist begründet.")
    assert page[1].startswith("1. Anspruch aus § 433 BGB ist begründet.")


# --- The paragraph loop ----------------------------------------------------

def test_a_line_end_hyphen_joins_the_word_unless_a_conjunction_follows():
    assert paragraphs(stack(
        "Der Kauf-", "vertrag ist wirksam, aber Kauf-",
        "und Werkvertrag unterscheiden sich.",
    )) == ["Der Kaufvertrag ist wirksam, aber Kauf- und Werkvertrag "
           "unterscheiden sich."]


def test_a_table_and_a_box_each_stand_apart():
    table = "| Frage | Antwort |\n| --- | --- |\n| Wer? | Der Käufer. |"
    assert paragraphs([
        z("Vorher steht ein Satz", y0=100),
        z(table, 100, 120, 900, height=60, container="tabelle"),
        z("Der Kasten beginnt hier", y0=190, container="kasten0"),
        z("und endet hier.", y0=208, container="kasten0"),
        z("Danach geht der Text weiter.", y0=226),
    ]) == ["Vorher steht ein Satz", table,
           "Der Kasten beginnt hier und endet hier.",
           "Danach geht der Text weiter."]


def test_a_question_and_answer_grid_becomes_one_table():
    questions = ["1. Was ist eine Vindikation?", "2. Wer ist Besitzer?",
                 "3. Was ist ein Anwartschaftsrecht?", "4. Was regelt § 985 BGB?",
                 "5. Wann ist die Anfechtung wirksam?", "6. Wer haftet?"]
    answers = ["Der Herausgabeanspruch.", "Wer die Sachherrschaft hat.",
               "Ein Recht auf Erwerb.", "Die Herausgabe.",
               "Mit Zugang der Erklärung.", "Der Schuldner."]
    lines = ([z(q, 100, 100 + 30 * i, 500) for i, q in enumerate(questions)]
             + [z(a, 520, 100 + 30 * i, 900) for i, a in enumerate(answers)])
    assert paragraphs(split_columns(lines)) == [
        "| Frage | Antwort |\n| --- | --- |\n" + "\n".join(
            f"| {q} | {a} |" for q, a in zip(questions, answers))]


# --- Text cleanup ----------------------------------------------------------

def test_latex_and_private_use_glyphs_become_plain_characters():
    assert paragraphs([
        z(r"$\rightarrow$ der Pfeil und \text{Text} \Rightarrow \to "
          "   \\underline{unterstrichen}"),
    ]) == ["→ der Pfeil und Text ⇒ → ⇨ • ▪ unterstrichen"]


def test_latex_footnote_mark_becomes_an_obsidian_mark():
    assert paragraphs([z(r"Der Besitzer haftet.^{12}")]) \
        == ["Der Besitzer haftet.[^12]"]


def test_inline_math_delimiters_are_dropped():
    assert paragraphs([z(r"Die Variable \(x\) bleibt.")]) \
        == ["Die Variable x bleibt."]


def test_misread_section_signs_and_roman_numerals_are_repaired():
    assert paragraphs([
        z("Nach $ 5 BGB und § § 929, § 854 | BGB sowie | BGB gilt das."),
    ]) == ["Nach § 5 BGB und §§ 929, § 854 I BGB sowie I BGB gilt das."]


def test_bold_markers_are_balanced_and_joined():
    assert paragraphs([z("**gesetzliches** **Schuldverhältnis** und **mehr")]) \
        == ["**gesetzliches Schuldverhältnis** und mehr"]


# --- Fragment merging (text layer) -----------------------------------------

def test_a_label_set_apart_from_its_line_is_one_text_layer_line():
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text(fitz.Point(54, 170), "1.", fontsize=10)
        page.insert_text(fitz.Point(90, 170), "Die Abtretung als Verfügung",
                         fontsize=10)
        page.insert_text(fitz.Point(54, 200), "weiterer Text auf anderer Zeile",
                         fontsize=10)
        lines = textlayer_lines(page)
    assert [line.text for line in lines] == [
        "1. Die Abtretung als Verfügung", "weiterer Text auf anderer Zeile"]

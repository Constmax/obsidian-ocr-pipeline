#!/usr/bin/env python3
"""A paragraph or heading must not split at a bold line, a lowercase
abbreviation, a box edge or a repeated label (Issue #163).

  python3 -m pytest pdf2md/test/test_paragraph_splits.py
"""
import pytest

from assembly import ENUMERATION, AssemblyContext, assemble_paragraphs

OCR = AssemblyContext(ocr_page=True)


def z(text, y0, x0=100, x1=900, marker=None):
    line = [text, (x0, y0, x1, y0 + 12)]
    return line + [marker] if marker else line


@pytest.mark.parametrize("text", [
    "a) Erster Punkt", "bb) Zweiter Punkt", "aaa) Dritte Ebene",
    "a. Erster Punkt", "1. Anspruch", "IV. Ergebnis", "- Spiegelstrich",
    "(2) Absatz", "iv. Ergebnis", "ff) Sechster Punkt",
])
def test_labels_start_an_enumeration(text):
    assert ENUMERATION.match(text)


@pytest.mark.parametrize("text", [
    "gem. § 823 I BGB", "vgl. BGH NJW 2000, 1", "hat.", "obj. Tb (-)",
    "i. V.m. § 15 BVerfSchG", "z. B. ein Kaufvertrag", "bzw. ein Vertrag",
    "ff. BGB", "o. ä. Gründe", "u. ä. Fälle",
])
def test_abbreviations_start_no_enumeration(text):
    assert not ENUMERATION.match(text)


def test_line_starting_with_abbreviation_continues():
    out = assemble_paragraphs([
        z("Der Kaeufer kann Schadensersatz verlangen, und zwar", 100),
        z("gem. § 280 I BGB, wenn der Verkaeufer die Pflicht verletzt", 112),
        z("hat.", 124, x1=150),
    ]).paragraphs
    assert out == ["Der Kaeufer kann Schadensersatz verlangen, und zwar "
                   "gem. § 280 I BGB, wenn der Verkaeufer die Pflicht "
                   "verletzt hat."]


def filler(y0):
    """A paragraph of four lines at 12-unit spacing, ending at y0 + 36."""
    return [z("Vorab ist festzuhalten, dass der Sachverhalt vollstaendig", y0),
            z("aufgeklaert ist und keine weiteren Tatsachen fehlen, die", y0 + 12),
            z("fuer die rechtliche Wuerdigung von Bedeutung sein", y0 + 24),
            z("koennten.", y0 + 36, x1=200)]


def bold_run_in_a_sentence():
    return filler(40) + [
        z("Der Mieter darf die Wohnung untervermieten, wenn es um einen",
          100, x1=750),
        z("**kurzen Zeitraum**", 112, x1=400),
        z("geht, vgl. § 540 I BGB.", 124, x1=400),
    ]


def test_bold_line_inside_a_sentence_continues_on_a_model_page():
    out = assemble_paragraphs(bold_run_in_a_sentence(), OCR).paragraphs
    assert out[1] == ("Der Mieter darf die Wohnung untervermieten, wenn es "
                      "um einen **kurzen Zeitraum** geht, vgl. § 540 I BGB.")


def test_bold_line_after_an_article_continues_on_a_model_page():
    lines = filler(40) + [
        z("Nach einer Ansicht folgt die", 100, x1=600),
        z("**Pflicht zur Herausgabe aus § 667 BGB**", 112, x1=600),
        z("unmittelbar.", 124, x1=200),
    ]
    out = assemble_paragraphs(lines, OCR).paragraphs
    assert out[1] == ("Nach einer Ansicht folgt die **Pflicht zur "
                      "Herausgabe aus § 667 BGB** unmittelbar.")


def test_bold_line_inside_a_sentence_still_splits_a_text_layer_page():
    assert len(assemble_paragraphs(bold_run_in_a_sentence()).paragraphs) == 4


def test_body_after_a_bold_heading_may_start_lowercase_on_a_model_page():
    out = assemble_paragraphs([
        z("Vorab ist festzuhalten:", 40, x1=300),
        z("Der Sachverhalt ist aufgeklaert.", 52, x1=500),
        z("**Meinungsstreit**", 76, x1=300),
        z("h.M.: Der Anspruch ist ausgeschlossen, weil", 88, x1=700),
        z("die Frist abgelaufen ist.", 100, x1=400),
    ], OCR).paragraphs
    assert out[1:] == ["**Meinungsstreit**",
                       "h.M.: Der Anspruch ist ausgeschlossen, weil die "
                       "Frist abgelaufen ist."]


def test_bold_heading_after_a_sentence_still_separates_on_a_model_page():
    out = assemble_paragraphs(filler(40) + [
        z("**II. Anspruch aus § 823 I BGB**", 100, x1=500),
        z("Der Anspruch setzt eine Rechtsgutsverletzung voraus.", 112),
    ], OCR).paragraphs
    assert out[1:] == ["### II. Anspruch aus § 823 I BGB",
                       "Der Anspruch setzt eine Rechtsgutsverletzung voraus."]


def test_wrapped_heading_ending_on_preposition_stays_one_on_a_model_page():
    out = assemble_paragraphs(filler(40) + [
        z("**1. Anspruch auf Kaufpreiszahlung aus**", 100, x1=500),
        z("**dem Kaufvertrag gem. § 433 II BGB**", 112, x1=500),
        z("Es muesste ein wirksamer Vertrag vorliegen.", 124, x1=600),
    ], OCR).paragraphs
    assert out[1:] == ["#### 1. Anspruch auf Kaufpreiszahlung aus dem "
                       "Kaufvertrag gem. § 433 II BGB",
                       "Es muesste ein wirksamer Vertrag vorliegen."]


def box_crossing():
    return [
        z("Ein Mangel liegt vor, wenn die gelieferte Ware eine", 100,
          marker="kasten1"),
        z("andere als die vereinbarte Farbe hat, also etwa", 112),
        z("blau statt rot ist.", 124, x1=400, marker="kasten2"),
    ]


def test_box_edge_inside_a_sentence_does_not_split_a_model_page():
    out = assemble_paragraphs(box_crossing(), OCR).paragraphs
    assert len(out) == 1


@pytest.mark.parametrize("last", [
    "Nach Ruecktritt kann der Kaeufer gegen den Verkaeufer",
    "Der Kaeufer kann, wenn die Frist abgelaufen ist,",
    "Der Kaeufer kann in diesem Fall die Ware zurueckgeben;",
])
def test_box_edge_mid_sentence_does_not_split_a_model_page(last):
    out = assemble_paragraphs([
        z(last, 100),
        z("Schadensersatz statt der Leistung verlangen.", 112, x1=600,
          marker="kasten1"),
    ], OCR).paragraphs
    assert len(out) == 1


def test_lowercase_line_after_a_period_crosses_a_box_edge_on_a_model_page():
    out = assemble_paragraphs([
        z("Der Anspruch ergibt sich aus § 812 I S. 1 Alt. 1 BGB i.V.m.", 100),
        z("§ 818 II BGB. vgl. dazu die Ausfuehrungen oben.", 112,
          x1=600),
        z("insoweit gilt dasselbe wie beim Herausgabeanspruch.", 124,
          x1=600, marker="kasten1"),
    ], OCR).paragraphs
    assert len(out) == 1


def test_box_edge_still_splits_a_text_layer_page():
    assert len(assemble_paragraphs(box_crossing()).paragraphs) == 3


@pytest.mark.parametrize("last", ["Es gilt folgendes Schema:",
                                  "Anspruchsgrundlage § 433 II BGB"])
def test_box_edge_after_a_colon_or_a_scheme_line_splits_a_model_page(last):
    out = assemble_paragraphs(filler(40) + [
        z(last, 100, x1=500),
        z("Voraussetzung ist ein wirksamer Vertrag.", 112, x1=500,
          marker="kasten1"),
    ], OCR).paragraphs
    assert out[1:] == [last, "Voraussetzung ist ein wirksamer Vertrag."]


def test_box_edge_after_a_sentence_splits_a_model_page():
    out = assemble_paragraphs([
        z("Die Ware war bei Uebergabe mangelfrei.", 100, x1=500),
        z("Ob der Kaeufer den Mangel spaeter verursacht hat, ist", 112,
          marker="kasten1"),
        z("durch Beweisaufnahme zu klaeren.", 124, x1=400, marker="kasten1"),
    ], OCR).paragraphs
    assert len(out) == 2


def test_label_read_twice_is_dropped():
    out = assemble_paragraphs([
        z("A. Vorbemerkung", 100, x1=400),
        ["2. Wann ist ein Vertrag ueber den Kauf einer", (60, 130, 800, 146)],
        ["**2.**", (62, 133, 90, 145)],
        ["Sache wirksam geschlossen?", (120, 147, 480, 159)],
    ], OCR).paragraphs
    assert out[-1] == ("2. Wann ist ein Vertrag ueber den Kauf einer "
                       "Sache wirksam geschlossen?")


def test_label_of_its_own_line_is_kept():
    out = assemble_paragraphs(filler(40) + [
        ["**1.**", (79, 100, 107, 112)],
        ["Begruendetheit", (139, 100, 400, 112)],
    ], OCR).paragraphs
    assert out[1] == "**1.**"


def test_abbreviation_at_line_end_continues_without_coordinates():
    out = assemble_paragraphs([
        ["Der Verein hat nichts erlangt, vgl.", None],
        ["§ 843 IV BGB.", None],
        ["Ein neuer Absatz beginnt hier.", None],
    ]).paragraphs
    assert out == ["Der Verein hat nichts erlangt, vgl. § 843 IV BGB.",
                   "Ein neuer Absatz beginnt hier."]

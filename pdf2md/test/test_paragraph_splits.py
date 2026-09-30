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
    "(2) Absatz",
])
def test_labels_start_an_enumeration(text):
    assert ENUMERATION.match(text)


@pytest.mark.parametrize("text", [
    "gem. § 823 I BGB", "vgl. BGH NJW 2000, 1", "hat.", "obj. Tb (-)",
    "i. V.m. § 15 BVerfSchG", "z. B. ein Kaufvertrag", "bzw. ein Vertrag",
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


def test_bold_line_inside_a_sentence_continues_on_a_model_page():
    lines = filler(40) + [
        z("Die Eltern duerfen allein handeln, wenn es um eine Angelegenheit",
          100, x1=750),
        z("**des taeglichen Lebens**", 112, x1=400),
        z("geht, vgl. § 1687 I S. 2 BGB.", 124, x1=400),
    ]
    out = assemble_paragraphs(lines, OCR).paragraphs
    assert out[1] == ("Die Eltern duerfen allein handeln, wenn es um eine "
                      "Angelegenheit **des taeglichen Lebens** geht, "
                      "vgl. § 1687 I S. 2 BGB.")


def test_bold_line_after_an_article_continues_on_a_model_page():
    lines = filler(40) + [
        z("Nach anderer Ansicht soll sich die", 100, x1=600),
        z("**Pflicht zur Abtretung aus § 242 BGB**", 112, x1=600),
        z("ergeben.", 124, x1=200),
    ]
    out = assemble_paragraphs(lines, OCR).paragraphs
    assert len(out) == 2


def test_bold_heading_after_a_sentence_still_separates_on_a_model_page():
    out = assemble_paragraphs(filler(40) + [
        z("**II. Anspruch aus § 823 I BGB**", 100, x1=500),
        z("Der Anspruch setzt eine Rechtsgutsverletzung voraus.", 112),
    ], OCR).paragraphs
    assert out[1:] == ["### II. Anspruch aus § 823 I BGB",
                       "Der Anspruch setzt eine Rechtsgutsverletzung voraus."]


def test_wrapped_heading_ending_on_preposition_stays_one_on_a_model_page():
    out = assemble_paragraphs(filler(40) + [
        z("**1. Aufwendungsersatzanspruch aus**", 100, x1=500),
        z("**GoA gem. §§ 677, 683, 670 BGB**", 112, x1=500),
        z("Es muesste ein fremdes Geschaeft vorliegen.", 124, x1=600),
    ], OCR).paragraphs
    assert out[1:] == ["#### 1. Aufwendungsersatzanspruch aus GoA gem. §§ 677, "
                       "683, 670 BGB",
                       "Es muesste ein fremdes Geschaeft vorliegen."]


def box_crossing():
    return [
        z("Eine Regelung liegt vor, wenn die Behoerde eine", 100,
          marker="kasten1"),
        z("verbindliche Rechtsfolge setzt, also Rechte", 112),
        z("begruendet oder aufhebt.", 124, x1=400, marker="kasten2"),
    ]


def test_box_edge_inside_a_sentence_does_not_split_a_model_page():
    out = assemble_paragraphs(box_crossing(), OCR).paragraphs
    assert len(out) == 1


def test_box_edge_still_splits_a_text_layer_page():
    assert len(assemble_paragraphs(box_crossing()).paragraphs) == 3


def test_box_edge_after_a_sentence_splits_a_model_page():
    out = assemble_paragraphs([
        z("Die Auskunft ist kein Verwaltungsakt.", 100, x1=500),
        z("Ob ein Schreiben eine Regelung enthaelt, ist", 112,
          marker="kasten1"),
        z("durch Auslegung zu ermitteln.", 124, x1=400, marker="kasten1"),
    ], OCR).paragraphs
    assert len(out) == 2


def test_label_read_twice_is_dropped():
    out = assemble_paragraphs([
        z("I. Prozessuales", 100, x1=400),
        ["1. Welche Klageart kommt gegen einen belastenden",
          (79, 133, 787, 150)],
        ["**1.**", (79, 140, 107, 149)],
        ["Verwaltungsakt in Betracht?", (139, 149, 488, 160)],
    ], OCR).paragraphs
    assert out[-1] == ("1. Welche Klageart kommt gegen einen belastenden "
                       "Verwaltungsakt in Betracht?")


def test_label_of_its_own_line_is_kept():
    out = assemble_paragraphs(filler(40) + [
        ["**1.**", (79, 100, 107, 112)],
        ["Begruendetheit", (139, 100, 400, 112)],
    ], OCR).paragraphs
    assert "1." in " ".join(out[1:])


def test_abbreviation_at_line_end_continues_without_coordinates():
    out = assemble_paragraphs([
        ["Der Verein hat nichts erlangt, vgl.", None],
        ["§ 843 IV BGB.", None],
        ["Ein neuer Absatz beginnt hier.", None],
    ]).paragraphs
    assert out == ["Der Verein hat nichts erlangt, vgl. § 843 IV BGB.",
                   "Ein neuer Absatz beginnt hier."]

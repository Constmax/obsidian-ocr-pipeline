#!/usr/bin/env python3
"""Margin labels: promote if in margin — otherwise flow into sentence.

  python3 -m pytest pdf2md/test/test_randmarke.py
"""
from zusammenbau import promote_margin_labels, assemble_paragraphs


def z(text, x0, y0, x1):
    return [text, (x0, y0, x1, y0 + 14), False]


def test_label_flows_into_sentence():
    out = assemble_paragraphs([
        z("**Beispiel:**", 166, 98, 237),
        z("Mutter M putzt gerade die Fenster ihrer Terrasse und stoesst dabei "
          "aus Unachtsamkeit einen", 166, 97, 899),
        z("Blumentopf herunter, welcher sodann den Dieb D trifft.",
          166, 116, 899)])
    assert len(out) == 1
    assert out[0].startswith("**Beispiel:** Mutter M putzt")
    assert out[0].endswith("den Dieb D trifft.")


def test_real_heading_still_separates():
    out = assemble_paragraphs([
        z("**A. Grundsaetzliches zum Unterlassen**", 166, 98, 600),
        z("Zu unterscheiden sind das unechte und das echte "
          "Unterlassungsdelikt.", 166, 120, 899)])
    assert len(out) == 2
    assert out[0].startswith("#")


def test_outdented_label_promoted():
    lines = [z("Der Garant ist zur Abwendung des Erfolges verpflichtet, so "
               "dass sein", 200, 100, 900),
             z("**Merke:**", 150, 118, 210),
             z("Unterlassen der Begehung durch aktives Tun entspricht, § 13 "
               "StGB.", 200, 118, 900),
             z("Daran schliesst sich die Frage der Garantenstellung an.",
               200, 136, 900),
             z("Sie ist der Kern jeder Unterlassungspruefung.", 200, 154, 900)]
    out = promote_margin_labels(lines, 18)
    assert out[0][0] == "**Merke:**"
    assert len(out) == len(lines)
    assert [x[0] for x in out[1:]] \
        == [x[0] for x in lines if x[0] != "**Merke:**"]


def test_label_on_body_indent_not_moved():
    lines = [z("Der Garant ist zur Abwendung des Erfolges verpflichtet.",
               200, 100, 900),
             z("**Merke:**", 200, 118, 260),
             z("Die Garantenstellung ist der Kern der Pruefung.",
               200, 136, 900),
             z("Sie folgt aus Gesetz, Vertrag oder Ingerenz.", 200, 154, 900)]
    assert [x[0] for x in promote_margin_labels(lines, 18)] \
        == [x[0] for x in lines]

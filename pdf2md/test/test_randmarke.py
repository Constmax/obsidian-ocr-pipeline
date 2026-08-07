#!/usr/bin/env python3
"""Randmarken: vorziehen, wenn sie im Rand stehen — sonst in den Satz einlaufen.

Kein Modell noetig. Zwei verschiedene Layouts erzeugen dieselbe Marke:

  * Hemmer-Skripte setzen "Beispiel:" ausgerueckt in den linken Rand, oft neben
    die zweite oder dritte Zeile des Absatzes → `randlabel_vorziehen` holt sie
    an den Anfang ihres Blocks.
  * Andere Seiten setzen sie als Vorspann derselben Zeile; das Modell gibt sie
    trotzdem als eigene Zeile aus → sie muss in den Folgesatz einlaufen und darf
    ihn nicht abreissen.

Der zweite Fall ist der teurere: als Ueberschrift gewertet zerlegt die Marke
einen Satz in zwei Absaetze, und der zweite beginnt mitten drin.

  python3 -m pytest pdf2md/test/test_randmarke.py
"""
from zusammenbau import randlabel_vorziehen, zusammenfuegen


def z(text, x0, y0, x1):
    return [text, (x0, y0, x1, y0 + 14), False]


def test_marke_laeuft_in_den_satz():
    raus = zusammenfuegen([
        z("**Beispiel:**", 166, 98, 237),
        z("Mutter M putzt gerade die Fenster ihrer Terrasse und stoesst dabei "
          "aus Unachtsamkeit einen", 166, 97, 899),
        z("Blumentopf herunter, welcher sodann den Dieb D trifft.",
          166, 116, 899)])
    assert len(raus) == 1
    assert raus[0].startswith("**Beispiel:** Mutter M putzt")
    assert raus[0].endswith("den Dieb D trifft.")


def test_echte_ueberschrift_trennt_weiterhin():
    raus = zusammenfuegen([
        z("**A. Grundsaetzliches zum Unterlassen**", 166, 98, 600),
        z("Zu unterscheiden sind das unechte und das echte "
          "Unterlassungsdelikt.", 166, 120, 899)])
    assert len(raus) == 2
    assert raus[0].startswith("#")


def test_ausgerueckte_marke_wird_vorgezogen():
    zeilen = [z("Der Garant ist zur Abwendung des Erfolges verpflichtet, so "
                "dass sein", 200, 100, 900),
              z("**Merke:**", 150, 118, 210),
              z("Unterlassen der Begehung durch aktives Tun entspricht, § 13 "
                "StGB.", 200, 118, 900),
              z("Daran schliesst sich die Frage der Garantenstellung an.",
                200, 136, 900),
              z("Sie ist der Kern jeder Unterlassungspruefung.", 200, 154, 900)]
    aus = randlabel_vorziehen(zeilen, 18)
    assert aus[0][0] == "**Merke:**"
    assert len(aus) == len(zeilen)
    assert [x[0] for x in aus[1:]] \
        == [x[0] for x in zeilen if x[0] != "**Merke:**"]


def test_marke_auf_rumpfeinzug_wird_nicht_verschoben():
    zeilen = [z("Der Garant ist zur Abwendung des Erfolges verpflichtet.",
                200, 100, 900),
              z("**Merke:**", 200, 118, 260),
              z("Die Garantenstellung ist der Kern der Pruefung.",
                200, 136, 900),
              z("Sie folgt aus Gesetz, Vertrag oder Ingerenz.", 200, 154, 900)]
    assert [x[0] for x in randlabel_vorziehen(zeilen, 18)] \
        == [x[0] for x in zeilen]

#!/usr/bin/env python3
"""Nahtentdopplung: der Fall, der im 40-Seiten-Lauf eine Seite gekostet hat.

Kein Modell noetig — die Regel arbeitet auf geparsten Zeilen.

  python3 -m pytest pdf2md/test/test_naht.py
"""
from ocr import ueberlappung_kuerzen


def z(text, y=0):
    return [text, (0, y, 1000, y + 10), False]


# 1. Der echte Fall: die Folgekachel wiederholt den Satzanfang und traegt den
#    Rest der Seite in DERSELBEN Absatzzeile. Frueher flog der ganze Absatz.
def test_fortsetzung_bleibt_erhalten():
    oben = [z("Er ist somit Besitzdiener iSd Paragraf 855 BGB. Infolgedessen "
              "ist L kein Besitzer und nur M ist Besitzer des Mehls. Ob es")]
    unten = [z("Infolgedessen ist L kein Besitzer und nur M ist Besitzer des "
               "Mehls. Ob es dem M abhanden gekommen ist, richtet sich nach "
               "Paragraf 935 BGB."),
             z("Fussnote 13: BGH NJW 2014, 1524.")]
    raus = ueberlappung_kuerzen(oben, unten)
    assert raus[0][0] == "dem M abhanden gekommen ist, richtet sich nach " \
                         "Paragraf 935 BGB."


def test_folgezeile_unangetastet():
    oben = [z("Er ist somit Besitzdiener iSd Paragraf 855 BGB. Infolgedessen "
              "ist L kein Besitzer und nur M ist Besitzer des Mehls. Ob es")]
    unten = [z("Infolgedessen ist L kein Besitzer und nur M ist Besitzer des "
               "Mehls. Ob es dem M abhanden gekommen ist, richtet sich nach "
               "Paragraf 935 BGB."),
             z("Fussnote 13: BGH NJW 2014, 1524.")]
    raus = ueberlappung_kuerzen(oben, unten)
    assert len(raus) == 2


# 2. Vollstaendig doppelte Zeile faellt ganz weg.
def test_ganze_dublette_entfaellt():
    oben = [z("Die Voraussetzungen des Paragraf 823 Absatz 1 BGB liegen vor.")]
    unten = [z("Die Voraussetzungen des Paragraf 823 Absatz 1 BGB liegen vor."),
             z("Ein Schaden ist entstanden.")]
    assert [x[0] for x in ueberlappung_kuerzen(oben, unten)] \
        == ["Ein Schaden ist entstanden."]


# 3. Kein Ueberlappungsband: nichts anfassen.
def test_ohne_naht_unveraendert():
    oben = [z("Der Anspruch ist nach Paragraf 985 BGB begruendet und faellig.")]
    unten = [z("Ein ganz anderer Gedanke beginnt hier ohne jede Wiederholung.")]
    assert [x[0] for x in ueberlappung_kuerzen(oben, unten)] \
        == ["Ein ganz anderer Gedanke beginnt hier ohne jede Wiederholung."]


# 4. Kurze Gliederungsmarker sind keine Naht — "aa)" darf mehrfach vorkommen.
def test_marker_loesen_keine_kuerzung_aus():
    oben = [z("aa) bb) cc) dd) ee) ff)")]
    unten = [z("aa) bb) cc) dd) ee) ff)"), z("Weiterer Text.")]
    assert len(ueberlappung_kuerzen(oben, unten)) == 2


# 5. Abweichende Erkennung im Band: lieber doppelt als verloren.
def test_rest_bleibt_trotz_lesefehler_erhalten():
    oben = [z("Anknuepfungspunkt waere also, dass H trotz des aus der "
              "Besitzverschaiung folgenden Anscheins nicht nachgeforscht "
              "hat.")]
    unten = [z("Anknuepfungspunkt waere also, dass H trotz des aus der "
               "Besitzverschaffung folgenden Anscheins nicht nachgeforscht "
               "hat. Fuer eine Nachforschungsobliegenheit spricht wenig.")]
    raus = ueberlappung_kuerzen(oben, unten)
    assert "Nachforschungsobliegenheit" in raus[0][0]


# 6. Leere Eingaben.
def test_leer_links():
    oben = [z("Er ist somit Besitzdiener iSd Paragraf 855 BGB. Infolgedessen "
              "ist L kein Besitzer und nur M ist Besitzer des Mehls. Ob es")]
    unten = [z("Infolgedessen ist L kein Besitzer und nur M ist Besitzer des "
               "Mehls. Ob es dem M abhanden gekommen ist, richtet sich nach "
               "Paragraf 935 BGB."),
             z("Fussnote 13: BGH NJW 2014, 1524.")]
    assert ueberlappung_kuerzen([], unten) == unten


def test_leer_rechts():
    oben = [z("Der Anspruch ist nach Paragraf 985 BGB begruendet und faellig.")]
    assert ueberlappung_kuerzen(oben, []) == []

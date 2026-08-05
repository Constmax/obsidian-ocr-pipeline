#!/usr/bin/env python3
"""Nahtentdopplung: der Fall, der im 40-Seiten-Lauf eine Seite gekostet hat.

  python3 .ocr-bench/test_naht.py

Kein Modell noetig — die Regel arbeitet auf geparsten Zeilen.
"""
import sys

import pfade                                  # legt pdf2md.py auf sys.path
import pdf2md as M

fehler = 0


def pruefe(name, ist, soll):
    global fehler
    if ist == soll:
        print(f"  ok   {name}")
    else:
        fehler += 1
        print(f"  FEHL {name}\n       ist : {ist!r}\n       soll: {soll!r}")


def z(text, y=0):
    return [text, (0, y, 1000, y + 10), False]


print("Nahtentdopplung")

# 1. Der echte Fall: die Folgekachel wiederholt den Satzanfang und traegt den
#    Rest der Seite in DERSELBEN Absatzzeile. Frueher flog der ganze Absatz.
oben = [z("Er ist somit Besitzdiener iSd Paragraf 855 BGB. Infolgedessen ist "
          "L kein Besitzer und nur M ist Besitzer des Mehls. Ob es")]
unten = [z("Infolgedessen ist L kein Besitzer und nur M ist Besitzer des "
           "Mehls. Ob es dem M abhanden gekommen ist, richtet sich nach "
           "Paragraf 935 BGB."),
         z("Fussnote 13: BGH NJW 2014, 1524.")]
raus = M.ueberlappung_kuerzen(oben, unten)
pruefe("Fortsetzung bleibt erhalten",
       raus[0][0], "dem M abhanden gekommen ist, richtet sich nach "
                   "Paragraf 935 BGB.")
pruefe("Folgezeile unangetastet", len(raus), 2)

# 2. Vollstaendig doppelte Zeile faellt ganz weg.
oben = [z("Die Voraussetzungen des Paragraf 823 Absatz 1 BGB liegen vor.")]
unten = [z("Die Voraussetzungen des Paragraf 823 Absatz 1 BGB liegen vor."),
         z("Ein Schaden ist entstanden.")]
pruefe("ganze Dublette entfaellt",
       [x[0] for x in M.ueberlappung_kuerzen(oben, unten)],
       ["Ein Schaden ist entstanden."])

# 3. Kein Ueberlappungsband: nichts anfassen.
oben = [z("Der Anspruch ist nach Paragraf 985 BGB begruendet und faellig.")]
unten = [z("Ein ganz anderer Gedanke beginnt hier ohne jede Wiederholung.")]
pruefe("ohne Naht unveraendert",
       [x[0] for x in M.ueberlappung_kuerzen(oben, unten)],
       ["Ein ganz anderer Gedanke beginnt hier ohne jede Wiederholung."])

# 4. Kurze Gliederungsmarker sind keine Naht — "aa)" darf mehrfach vorkommen.
oben = [z("aa) bb) cc) dd) ee) ff)")]
unten = [z("aa) bb) cc) dd) ee) ff)"), z("Weiterer Text.")]
pruefe("Marker loesen keine Kuerzung aus",
       len(M.ueberlappung_kuerzen(oben, unten)), 2)

# 5. Abweichende Erkennung im Band: lieber doppelt als verloren.
oben = [z("Anknuepfungspunkt waere also, dass H trotz des aus der "
          "Besitzverschaiung folgenden Anscheins nicht nachgeforscht hat.")]
unten = [z("Anknuepfungspunkt waere also, dass H trotz des aus der "
           "Besitzverschaffung folgenden Anscheins nicht nachgeforscht hat. "
           "Fuer eine Nachforschungsobliegenheit spricht wenig.")]
raus = M.ueberlappung_kuerzen(oben, unten)
pruefe("Rest bleibt trotz Lesefehler erhalten",
       "Nachforschungsobliegenheit" in raus[0][0], True)

# 6. Leere Eingaben.
pruefe("leer links", M.ueberlappung_kuerzen([], unten), unten)
pruefe("leer rechts", M.ueberlappung_kuerzen(oben, []), [])

print(f"\n{'alles gruen' if not fehler else str(fehler) + ' Fehler'}")
sys.exit(1 if fehler else 0)

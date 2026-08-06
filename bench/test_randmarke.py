#!/usr/bin/env python3
"""Randmarken: vorziehen, wenn sie im Rand stehen — sonst in den Satz einlaufen.

  python3 .ocr-bench/test_randmarke.py

Kein Modell noetig. Zwei verschiedene Layouts erzeugen dieselbe Marke:

  * Hemmer-Skripte setzen "Beispiel:" ausgerueckt in den linken Rand, oft neben
    die zweite oder dritte Zeile des Absatzes → `randlabel_vorziehen` holt sie
    an den Anfang ihres Blocks.
  * Andere Seiten setzen sie als Vorspann derselben Zeile; das Modell gibt sie
    trotzdem als eigene Zeile aus → sie muss in den Folgesatz einlaufen und darf
    ihn nicht abreissen.

Der zweite Fall ist der teurere: als Ueberschrift gewertet zerlegt die Marke
einen Satz in zwei Absaetze, und der zweite beginnt mitten drin.
"""
import sys

import pfade                                  # legt pdf2md/ auf sys.path
import zusammenbau as Z

fehler = 0


def pruefe(name, ist, soll):
    global fehler
    if ist == soll:
        print(f"  ok   {name}")
    else:
        fehler += 1
        print(f"  FEHL {name}\n       ist : {ist!r}\n       soll: {soll!r}")


def z(text, x0, y0, x1):
    return [text, (x0, y0, x1, y0 + 14), False]


print("Marke auf gleicher Hoehe wie der Rumpf → laeuft in den Satz")
raus = Z.zusammenfuegen([
    z("**Beispiel:**", 166, 98, 237),
    z("Mutter M putzt gerade die Fenster ihrer Terrasse und stoesst dabei "
      "aus Unachtsamkeit einen", 166, 97, 899),
    z("Blumentopf herunter, welcher sodann den Dieb D trifft.", 166, 116, 899)])
pruefe("ein einziger Absatz", len(raus), 1)
pruefe("Marke steht vorn",
       raus[0].startswith("**Beispiel:** Mutter M putzt"), True)
pruefe("Satz bleibt ganz", raus[0].endswith("den Dieb D trifft."), True)

print("\nEchte Ueberschrift trennt weiterhin")
raus = Z.zusammenfuegen([
    z("**A. Grundsaetzliches zum Unterlassen**", 166, 98, 600),
    z("Zu unterscheiden sind das unechte und das echte Unterlassungsdelikt.",
      166, 120, 899)])
pruefe("zwei Absaetze", len(raus), 2)
pruefe("als Ueberschrift ausgezeichnet", raus[0].startswith("#"), True)

print("\nAusgerueckte Marke wird vorgezogen")
zeilen = [z("Der Garant ist zur Abwendung des Erfolges verpflichtet, so dass "
            "sein", 200, 100, 900),
          z("**Merke:**", 150, 118, 210),
          z("Unterlassen der Begehung durch aktives Tun entspricht, § 13 "
            "StGB.", 200, 118, 900),
          z("Daran schliesst sich die Frage der Garantenstellung an.",
            200, 136, 900),
          z("Sie ist der Kern jeder Unterlassungspruefung.", 200, 154, 900)]
aus = Z.randlabel_vorziehen(zeilen, 18)
pruefe("Marke jetzt an erster Stelle", aus[0][0], "**Merke:**")
pruefe("keine Zeile verloren", len(aus), len(zeilen))
pruefe("Rumpf unveraendert sortiert",
       [x[0] for x in aus[1:]], [x[0] for x in zeilen if x[0] != "**Merke:**"])

print("\nMarke auf Rumpfeinzug wird NICHT verschoben")
zeilen = [z("Der Garant ist zur Abwendung des Erfolges verpflichtet.",
            200, 100, 900),
          z("**Merke:**", 200, 118, 260),
          z("Die Garantenstellung ist der Kern der Pruefung.", 200, 136, 900),
          z("Sie folgt aus Gesetz, Vertrag oder Ingerenz.", 200, 154, 900)]
pruefe("Reihenfolge unangetastet",
       [x[0] for x in Z.randlabel_vorziehen(zeilen, 18)],
       [x[0] for x in zeilen])

print(f"\n{'alles gruen' if not fehler else str(fehler) + ' Fehler'}")
sys.exit(1 if fehler else 0)

#!/usr/bin/env python3
"""Schleifenerkennung und -kuerzung, woertlich wie ziffernblind.

  python3 .ocr-bench/test_schleife.py

Kein Modell noetig. Prueft die zwei Bauformen, die im Benchmark vorkamen —
wiederholte Wortfolge und aufsteigender Zaehler — und die Faelle, die NICHT
angefasst werden duerfen.
"""
import sys

import pfade                                  # legt pdf2md/ auf sys.path
import ocr as O

fehler = 0


def pruefe(name, ist, soll):
    global fehler
    if ist == soll:
        print(f"  ok   {name}")
    else:
        fehler += 1
        print(f"  FEHL {name}\n       ist : {ist!r}\n       soll: {soll!r}")


def z(text):
    return [text, (0, 0, 1000, 10), False]


print("schleifenlaenge")
pruefe("gesunder Satz",
       O.schleifenlaenge("Der Anspruch des K gegen B ergibt sich aus dem "
                         "Kaufvertrag nach Paragraf 433 Absatz 2 BGB.") < 2,
       True)
pruefe("woertliche Schleife",
       O.schleifenlaenge(" ".join(["a b c d e"] * 30)) >= O.SCHLEIFE_AB, True)
zaehler = " ".join(f"({j})" for j in range(1982, 2260))
pruefe("Zaehlschleife ziffernblind erkannt",
       O.schleifenlaenge(zaehler) >= O.SCHLEIFE_AB, True)
pruefe("Fussnotenblock schlaegt nicht an",
       O.schleifenlaenge(
           "BGH, NJW 2014, 1524 Rn. 8 m.w.N. "
           "Staudinger/Heinze, Paragraf 935 Rn. 14. "
           "MueKoBGB/Schaefer, Paragraf 855 Rn. 25. "
           "Vgl. MueKoBGB/Oechsler, Paragraf 935 Rn. 10 m.w.N.") < O.SCHLEIFE_AB,
       True)

print("\nschleife_kuerzen")
raus = O.schleife_kuerzen([z(zaehler)])
worte = raus[0][0].split()
pruefe("Zaehler auf zwei Vorkommen", len(worte), 2)
pruefe("echte Werte, keine Kopie", worte, ["(1982)", "(1983)"])

pruefe("woertliche Wiederholung gekuerzt",
       O.schleife_kuerzen([z(" ".join(["V."] * 40))])[0][0], "V. V.")

kette = "Ansprueche aus Paragraf 823, Paragraf 826, Paragraf 831, Paragraf 840."
pruefe("Normenkette bleibt unangetastet",
       O.schleife_kuerzen([z(kette)])[0][0], kette)

lang = ("Der Erfolg ist objektiv zurechenbar, wenn der Taeter eine rechtlich "
        "missbilligte Gefahr geschaffen hat, die sich im tatbestandsmaessigen "
        "Erfolg verwirklicht. Das ist hier der Fall, weil A den B mit dem "
        "Messer verletzte und die Wunde unmittelbar zum Tod fuehrte.")
pruefe("Fliesstext bleibt unangetastet",
       O.schleife_kuerzen([z(lang)])[0][0], lang)

pruefe("gleiche Zeile hundertfach → zwei",
       len(O.schleife_kuerzen([z("Dieselbe Fussnote steht hier.")] * 100)), 2)

print(f"\n{'alles gruen' if not fehler else str(fehler) + ' Fehler'}")
sys.exit(1 if fehler else 0)

#!/usr/bin/env python3
"""Schleifenerkennung und -kuerzung, woertlich wie ziffernblind.

Kein Modell noetig. Prueft die zwei Bauformen, die im Benchmark vorkamen —
wiederholte Wortfolge und aufsteigender Zaehler — und die Faelle, die NICHT
angefasst werden duerfen.

  python3 -m pytest pdf2md/test/test_schleife.py
"""
from ocr import SCHLEIFE_AB, schleifenlaenge, schleife_kuerzen


def z(text):
    return [text, (0, 0, 1000, 10), False]


def test_gesunder_satz():
    assert schleifenlaenge("Der Anspruch des K gegen B ergibt sich aus dem "
                           "Kaufvertrag nach Paragraf 433 Absatz 2 BGB.") < 2


def test_woertliche_schleife():
    assert schleifenlaenge(" ".join(["a b c d e"] * 30)) >= SCHLEIFE_AB


def test_zaehlschleife_ziffernblind_erkannt():
    zaehler = " ".join(f"({j})" for j in range(1982, 2260))
    assert schleifenlaenge(zaehler) >= SCHLEIFE_AB


def test_fussnotenblock_schlaegt_nicht_an():
    assert schleifenlaenge(
        "BGH, NJW 2014, 1524 Rn. 8 m.w.N. "
        "Staudinger/Heinze, Paragraf 935 Rn. 14. "
        "MueKoBGB/Schaefer, Paragraf 855 Rn. 25. "
        "Vgl. MueKoBGB/Oechsler, Paragraf 935 Rn. 10 m.w.N.") < SCHLEIFE_AB


def test_zaehler_auf_zwei_vorkommen():
    zaehler = " ".join(f"({j})" for j in range(1982, 2260))
    raus = schleife_kuerzen([z(zaehler)])
    worte = raus[0][0].split()
    assert len(worte) == 2


def test_zaehler_behaelt_echte_werte():
    zaehler = " ".join(f"({j})" for j in range(1982, 2260))
    raus = schleife_kuerzen([z(zaehler)])
    assert raus[0][0].split() == ["(1982)", "(1983)"]


def test_woertliche_wiederholung_gekuerzt():
    assert schleife_kuerzen([z(" ".join(["V."] * 40))])[0][0] == "V. V."


def test_normenkette_bleibt_unangetastet():
    kette = "Ansprueche aus Paragraf 823, Paragraf 826, Paragraf 831, " \
            "Paragraf 840."
    assert schleife_kuerzen([z(kette)])[0][0] == kette


def test_fliesstext_bleibt_unangetastet():
    lang = ("Der Erfolg ist objektiv zurechenbar, wenn der Taeter eine "
            "rechtlich missbilligte Gefahr geschaffen hat, die sich im "
            "tatbestandsmaessigen Erfolg verwirklicht. Das ist hier der Fall, "
            "weil A den B mit dem Messer verletzte und die Wunde unmittelbar "
            "zum Tod fuehrte.")
    assert schleife_kuerzen([z(lang)])[0][0] == lang


def test_gleiche_zeile_hundertfach():
    assert len(schleife_kuerzen([z("Dieselbe Fussnote steht hier.")] * 100)) \
        == 2

"""Umrechnung Layoutregionen -> Spaltenentscheidung (bench/layoutmodell_test.py).

Das Modell selbst braucht Gewichte, torch und opencv und laeuft darum nur im
Vault. Die Umrechnung seiner Regionen in ('einspaltig'|'zweispaltig', steg) ist
dagegen reine Geometrie — und genau die Stelle, an der die Vergleichbarkeit mit
`layout_erkennen()` haengt. Sie gehoert deshalb unter CI, auch wenn der Rest des
Messstands es nicht kann.

Geprueft werden die beiden Fallen, an denen die Heuristik historisch
gescheitert ist (Nachtrag 5 und 9 in bench/ERGEBNIS.md): eine zentrierte Zeile
ist keine zweite Spalte, und eine vollbreite Kopfzeile verdeckt keinen Steg.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bench"))

from layoutmodell_test import spalten_aus_regionen      # noqa: E402

BREITE = 1000.0


def reg(klasse, x0, x1, y0=100, y1=400, score=0.9):
    return (klasse, x0, y0, x1, y1, score)


@pytest.mark.parametrize("name,regionen,erwartet", [
    ("echter Zweispalter", [
        reg("text", 60, 470), reg("text", 530, 940),
        reg("text", 60, 470, 420, 700), reg("text", 530, 940, 420, 700),
    ], "zweispaltig"),
    ("Zweispalter unter vollbreiter Kopfzeile", [
        reg("header", 60, 940, 20, 50),
        reg("paragraph_title", 60, 940, 60, 90),
        reg("text", 60, 470), reg("text", 530, 940),
    ], "zweispaltig"),
    ("einspaltig", [
        reg("text", 80, 920), reg("text", 80, 920, 420, 700),
    ], "einspaltig"),
    ("einspaltig mit schmalem Satzspiegel", [
        reg("text", 80, 640), reg("text", 80, 640, 420, 700),
    ], "einspaltig"),
    ("zentrierte Zeile ist keine Spalte", [
        reg("text", 80, 920), reg("paragraph_title", 380, 655, 300, 330),
        reg("text", 80, 920, 420, 700),
    ], "einspaltig"),
    ("nur Rand-, keine Satzregionen", [
        reg("header", 60, 940, 20, 50), reg("footer", 60, 940, 950, 980),
    ], "einspaltig"),
    ("vollbreite Tabelle spannt ueber den Steg", [
        reg("table", 60, 940), reg("text", 60, 940, 420, 700),
    ], "einspaltig"),
    ("leere Seite", [], "einspaltig"),
    ("eine einzelne Region", [reg("text", 60, 470)], "einspaltig"),
])
def test_spaltenentscheidung(name, regionen, erwartet):
    art, _ = spalten_aus_regionen(regionen, BREITE)
    assert art == erwartet, name


def test_steg_liegt_in_der_luecke():
    """Die Stegposition muss zwischen den Spalten liegen, nicht irgendwo."""
    art, steg = spalten_aus_regionen([
        reg("text", 60, 470), reg("text", 530, 940),
    ], BREITE)
    assert art == "zweispaltig"
    assert 0.47 <= steg <= 0.53


def test_randklassen_belegen_keine_spalte():
    """Eine Seitenzahl im Bund darf den Steg nicht zerlegen (Nachtrag 9)."""
    ohne = spalten_aus_regionen([
        reg("text", 60, 470), reg("text", 530, 940),
    ], BREITE)
    mit = spalten_aus_regionen([
        reg("text", 60, 470), reg("text", 530, 940),
        reg("number", 490, 510, 960, 980),
    ], BREITE)
    assert ohne == mit

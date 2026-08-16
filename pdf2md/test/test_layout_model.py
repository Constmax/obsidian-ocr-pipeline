"""Conversion of layout regions -> column decision (bench/layoutmodell_test.py).

The model itself requires weights, torch, and opencv, so it only runs in the Vault.
The conversion of its regions into ('einspaltig'|'zweispaltig', gutter) is pure
geometry — and precisely where comparability with `detect_layout()` lies.
It therefore belongs under CI even if the rest of the benchmark suite cannot run there.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bench"))

from layoutmodell_test import spalten_aus_regionen      # noqa: E402

WIDTH = 1000.0


def reg(cls, x0, x1, y0=100, y1=400, score=0.9):
    return (cls, x0, y0, x1, y1, score)


@pytest.mark.parametrize("name,regions,expected", [
    ("real two-column", [
        reg("text", 60, 470), reg("text", 530, 940),
        reg("text", 60, 470, 420, 700), reg("text", 530, 940, 420, 700),
    ], "zweispaltig"),
    ("two-column under full-width header", [
        reg("header", 60, 940, 20, 50),
        reg("paragraph_title", 60, 940, 60, 90),
        reg("text", 60, 470), reg("text", 530, 940),
    ], "zweispaltig"),
    ("single-column", [
        reg("text", 80, 920), reg("text", 80, 920, 420, 700),
    ], "einspaltig"),
    ("single-column with narrow type area", [
        reg("text", 80, 640), reg("text", 80, 640, 420, 700),
    ], "einspaltig"),
    ("centered line is not a column", [
        reg("text", 80, 920), reg("paragraph_title", 380, 655, 300, 330),
        reg("text", 80, 920, 420, 700),
    ], "einspaltig"),
    ("only margin, no body regions", [
        reg("header", 60, 940, 20, 50), reg("footer", 60, 940, 950, 980),
    ], "einspaltig"),
    ("full-width table spans over gutter", [
        reg("table", 60, 940), reg("text", 60, 940, 420, 700),
    ], "einspaltig"),
    ("empty page", [], "einspaltig"),
    ("single region", [reg("text", 60, 470)], "einspaltig"),
])
def test_column_decision(name, regions, expected):
    layout_type, _ = spalten_aus_regionen(regions, WIDTH)
    assert layout_type == expected, name


def test_gutter_lies_in_gap():
    """Gutter position must lie between columns."""
    layout_type, gutter = spalten_aus_regionen([
        reg("text", 60, 470), reg("text", 530, 940),
    ], WIDTH)
    assert layout_type == "zweispaltig"
    assert 0.47 <= gutter <= 0.53


def test_margin_classes_do_not_occupy_column():
    """A page number in the margin must not split the gutter."""
    without_num = spalten_aus_regionen([
        reg("text", 60, 470), reg("text", 530, 940),
    ], WIDTH)
    with_num = spalten_aus_regionen([
        reg("text", 60, 470), reg("text", 530, 940),
        reg("number", 490, 510, 960, 980),
    ], WIDTH)
    assert without_num == with_num

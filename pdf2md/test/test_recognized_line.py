"""The recognized-line record from recognition to page block (Issue #144).

  python3 -m pytest pdf2md/test/test_recognized_line.py
"""
from pathlib import Path

import pytest

import conversion
import page_cache
from assembly import (AssemblyContext, RecognizedLine, assemble_paragraphs,
                      parse_lines)
from conversion import ConversionRequest, convert_document
from layout import assign_boxes
from ocr import OVERLAP, tile_lines


def _loc(x0, y0, x1, y1):
    return "".join(f"<|LOC_{value}|>"
                   for value in (x0, y0, x1, y0, x1, y1, x0, y1))


def _tile_lines(image, max_tokens=None):
    tile = Path(image).stem.rsplit("_", 1)[-1]
    return "\n".join(_loc(100, y, 900, y + 20) + f"Zeile {tile} {y}"
                     for y in (100, 500))


def _cached_lines(tmp_path, make_vector_pdf, monkeypatch, layout, tile_from,
                  fake_ocr=_tile_lines):
    """Convert one page through the tile loop with a fake model and return
    the recognized lines its page-cache entry holds."""
    monkeypatch.setattr(conversion, "detect_layout", lambda _page: layout)
    pdf = tmp_path / "scan.pdf"
    make_vector_pdf(pdf, pages=1)

    output = tmp_path / "output"
    convert_document(ConversionRequest(
        pdf=pdf, output_dir=output, ocr_only=True, retries=0,
        tile_from=tile_from, temp_root=tmp_path / "scratch",
        no_dictionary=True), fake_ocr)
    entry = page_cache.read_latest_page(
        page_cache.cache_directory(output, pdf), 1)
    return entry["mode"], page_cache.recognized_lines(entry)


def test_model_output_becomes_lines_with_their_box_in_the_tile():
    text = (_loc(100, 210, 880, 225) + "Er ist Besitzdiener.\n"
            "\n" + _loc(120, 290, 140, 305) + "   \n"
            "Eine Zeile ohne Koordinaten.")
    assert parse_lines(text) == [
        RecognizedLine("Er ist Besitzdiener.", (100, 210, 880, 225)),
        RecognizedLine("Eine Zeile ohne Koordinaten.")]


def test_a_vertical_tile_gives_its_lines_its_index_as_column(
        tmp_path, make_vector_pdf, monkeypatch):
    mode, lines = _cached_lines(tmp_path, make_vector_pdf, monkeypatch,
                                ("zweispaltig", 0.5), tile_from=3000)
    assert mode == "senkrecht @50%"
    assert [(line.text, line.column) for line in lines] == [
        ("Zeile L 100", 0), ("Zeile L 500", 0),
        ("Zeile R 100", 1), ("Zeile R 500", 1)]


def test_horizontal_tiles_leave_the_column_unknown(
        tmp_path, make_vector_pdf, monkeypatch):
    mode, lines = _cached_lines(tmp_path, make_vector_pdf, monkeypatch,
                                ("einspaltig", None), tile_from=0)
    assert mode == "waagerecht"
    assert [line.column for line in lines] == [None] * 4


def _whole_tile(image, max_tokens=None):
    """One line per tile whose box is the whole tile."""
    tile = Path(image).stem.rsplit("_", 1)[-1]
    return _loc(0, 0, 1000, 1000) + f"Kachel {tile}"


@pytest.mark.parametrize("layout, tile_from, axis", [
    (("zweispaltig", 0.5), 3000, 0),
    (("einspaltig", None), 0, 1),
])
def test_lines_of_both_tiles_end_in_page_coordinates(
        tmp_path, make_vector_pdf, monkeypatch, layout, tile_from, axis):
    _, lines = _cached_lines(tmp_path, make_vector_pdf, monkeypatch, layout,
                             tile_from, _whole_tile)
    cut, overlap = 500, OVERLAP * 1000
    first, second = (line.box for line in lines)
    across = 1 - axis
    # Along the cut each tile spans its part of the page plus the overlap;
    # across the cut it spans the whole page.
    assert first[axis] == 0
    assert first[axis + 2] == pytest.approx(cut + overlap, abs=2)
    assert second[axis] == pytest.approx(cut - overlap, abs=2)
    assert second[axis + 2] == 1000
    assert (first[across], first[across + 2]) == (0, 1000)
    assert (second[across], second[across + 2]) == (0, 1000)


def test_assign_boxes_sets_the_container():
    table = RecognizedLine("| a | b |", (100, 100, 900, 200), container="tabelle")
    inside = RecognizedLine("im Kasten", (120, 320, 400, 330), column=0)
    outside = RecognizedLine("draußen", (120, 700, 400, 710))
    no_box = RecognizedLine("ohne Box")
    boxes = [(0, 0, 50, 50), (100, 300, 500, 400)]

    out = assign_boxes([table, inside, outside, no_box], boxes)

    assert out == [table, RecognizedLine("im Kasten", (120, 320, 400, 330),
                                         column=0, container="kasten1"),
                   outside, no_box]
    # Beside a box at the same height is not inside it.
    assert assign_boxes([outside], [(600, 690, 900, 720)]) == [outside]


def test_discarded_lines_carry_their_reason():
    lines = [RecognizedLine("Skript Schuldrecht AT", (100, 20, 500, 40)),
             RecognizedLine("Juristisches Repetitorium", (100, 45, 500, 60)),
             RecognizedLine("Der Anspruch ist begründet.", (100, 300, 900, 315)),
             RecognizedLine("17", (480, 960, 520, 975))]
    result = assemble_paragraphs(
        lines, AssemblyContext(frozenset({"Skript Schuldrecht AT"})))

    assert result.paragraphs == ["Der Anspruch ist begründet."]
    assert result.discarded == [(lines[0], "running_line"),
                                (lines[1], "boilerplate"),
                                (lines[3], "page_number")]


def test_a_retried_tile_maps_its_halves_back_to_the_tile(tmp_path):
    from PIL import Image

    image = tmp_path / "tile.png"
    Image.new("RGB", (400, 600), "white").save(image)

    def fake_ocr(path, max_tokens=None):
        if Path(path).stem == "tile":
            return "\n".join([_loc(100, 100, 900, 120) + "immer dasselbe"] * 30)
        half = Path(path).stem.rsplit("_", 1)[-1]
        return _loc(100, 0, 900, 1000) + f"Hälfte {half} mit eigenem Text"

    lines, trace = tile_lines(image, fake_ocr, None, 200)

    assert "retiled" in trace[-1]
    top, bottom = (line.box for line in lines)
    overlap = OVERLAP * 1000
    assert (top[0], top[2], bottom[0], bottom[2]) == (100, 900, 100, 900)
    assert top[1] == 0 and bottom[3] == 1000
    assert top[3] == pytest.approx(500 + overlap, abs=2)
    assert bottom[1] == pytest.approx(500 - overlap, abs=2)

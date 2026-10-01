"""The recognized-line record from recognition to page block (Issue #144).

  python3 -m pytest pdf2md/test/test_recognized_line.py
"""
from pathlib import Path

import pytest

import conversion
import page_cache
from assembly import AssemblyContext, RecognizedLine, assemble_paragraphs
from conversion import ConversionRequest, convert_document, tile_local_axis
from layout import assign_boxes


def _loc(x0, y0, x1, y1):
    return "".join(f"<|LOC_{value}|>"
                   for value in (x0, y0, x1, y0, x1, y1, x0, y1))


def _cached_lines(tmp_path, make_vector_pdf, monkeypatch, layout, tile_from):
    """Convert one page through the tile loop with a fake model and return
    the recognized lines its page-cache entry holds."""
    monkeypatch.setattr(conversion, "detect_layout", lambda _page: layout)
    pdf = tmp_path / "scan.pdf"
    make_vector_pdf(pdf, pages=1)

    def fake_ocr(image, max_tokens=None):
        tile = Path(image).stem.rsplit("_", 1)[-1]
        return "\n".join(_loc(100, y, 900, y + 20) + f"Zeile {tile} {y}"
                         for y in (100, 500))

    output = tmp_path / "output"
    convert_document(ConversionRequest(
        pdf=pdf, output_dir=output, ocr_only=True, retries=0,
        tile_from=tile_from, temp_root=tmp_path / "scratch",
        no_dictionary=True), fake_ocr)
    entry = page_cache.read_latest_page(
        page_cache.cache_directory(output, pdf), 1)
    return entry["mode"], page_cache.recognized_lines(entry)


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


@pytest.mark.parametrize("mode, axis", [
    ("textlayer", None), ("ganz", None), ("image-only", None),
    ("senkrecht @50%", "x"), ("waagerecht", "y"),
])
def test_tile_local_axis_follows_the_mode(mode, axis):
    assert tile_local_axis(mode) == axis


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
    # A vertical tile matches a box by height within the tile's x range.
    assert assign_boxes([outside], [(600, 690, 900, 720)], (500, 1000)) \
        == [RecognizedLine("draußen", (120, 700, 400, 710),
                           container="kasten0")]


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

"""Which order reaches the OCRmyPDF text layer: hOCR element order or geometry?

The Stage-1 quality gate reads text with `pdftotext -raw`, which follows the
PDF content stream. These tests pin down how OCRmyPDF orders that stream for a
two-column page whose hOCR element order deliberately differs from its
geometry (issue #61, docs/paddle-textlayer.md step 0).

Observed with OCRmyPDF 17.8.0: the content stream follows hOCR element order
exactly, both in the fpdf2 renderer alone and through the full pipeline
(rasterize, render, graft). Geometry reorders nothing, so an OCR engine plugin
owns reading order: a right column emitted first is read first.

Requirements: ocrmypdf==17.8.0 (the renderer is not public API), pdftotext,
and for the pipeline test the tesseract binary — OCRmyPDF 17.8.0 checks for it
even when an engine plugin does the recognition, and blocking its built-in
Tesseract plugin breaks option handling. Missing requirements skip the tests,
unless REQUIRE_OCRMYPDF=1 (set in CI), which turns every skip into a failure.

Run with: python3 -m pytest bin/test/test_hocr_text_layer_order.py -q
"""
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
STUB = HERE / "hocr_order_stub_engine.py"
PINNED_OCRMYPDF = "17.8.0"

CASES = {
    "columns": [("L", 0), ("L", 1), ("L", 2), ("R", 0), ("R", 1), ("R", 2)],
    "rows": [("L", 0), ("R", 0), ("L", 1), ("R", 1), ("L", 2), ("R", 2)],
    "right_column_first": [("R", 0), ("R", 1), ("R", 2), ("L", 0), ("L", 1), ("L", 2)],
}


def _require(condition, reason):
    if condition:
        return
    if os.environ.get("REQUIRE_OCRMYPDF") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def ocrmypdf():
    try:
        import ocrmypdf as module
    except ImportError:
        _require(False, "ocrmypdf is not installed")
    _require(module.__version__ == PINNED_OCRMYPDF,
             f"ocrmypdf {module.__version__} is installed; these tests pin "
             f"{PINNED_OCRMYPDF} because the fpdf2 renderer is not public API")
    return module


@pytest.fixture(scope="module")
def stub(ocrmypdf):
    spec = importlib.util.spec_from_file_location("hocr_order_stub_engine", STUB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pdftotext():
    path = shutil.which("pdftotext")
    _require(path is not None, "pdftotext (poppler) is not on PATH")
    return path


def _raw_words(pdftotext, pdf):
    result = subprocess.run([pdftotext, "-raw", str(pdf), "-"],
                            capture_output=True, text=True, check=True)
    return result.stdout.split()


@pytest.mark.parametrize("order", CASES.values(), ids=CASES.keys())
def test_fpdf2_renderer_follows_hocr_element_order(tmp_path, ocrmypdf, stub,
                                                   pdftotext, order):
    from ocrmypdf.font import MultiFontManager
    from ocrmypdf.fpdf_renderer import Fpdf2PdfRenderer
    from ocrmypdf.hocrtransform.hocr_parser import HocrParser

    hocr = tmp_path / "page.hocr"
    hocr.write_text(stub.build_hocr(order, stub.REF_W, stub.REF_H), encoding="utf-8")
    pdf = tmp_path / "page.pdf"
    fonts = MultiFontManager(Path(ocrmypdf.__file__).parent / "data")
    Fpdf2PdfRenderer(page=HocrParser(hocr).parse(), dpi=300,
                     multi_font_manager=fonts, invisible_text=True).render(pdf)

    assert _raw_words(pdftotext, pdf) == stub.words(order)


@pytest.mark.parametrize("name", ["columns", "right_column_first"])
def test_pipeline_text_layer_follows_hocr_element_order(tmp_path, monkeypatch,
                                                        ocrmypdf, stub,
                                                        pdftotext, name):
    _require(shutil.which("tesseract") is not None,
             "tesseract is not on PATH; OCRmyPDF 17.8.0 requires it even "
             "when an engine plugin does the recognition")
    from PIL import Image

    order = CASES[name]
    source = tmp_path / "blank.pdf"
    Image.new("RGB", (stub.REF_W, stub.REF_H), "white").save(source, "PDF", resolution=300)
    output = tmp_path / "ocr.pdf"
    monkeypatch.setenv("HOCR_ORDER", json.dumps(order))

    exit_code = ocrmypdf.ocr(source, output, plugins=[str(STUB)], language=["deu"],
                             force_ocr=True, output_type="pdf", optimize=0,
                             rasterizer="pypdfium", jobs=1, progress_bar=False)

    assert exit_code == 0
    assert _raw_words(pdftotext, output) == stub.words(order)

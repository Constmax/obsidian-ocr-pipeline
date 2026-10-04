"""Stage 1 keeps an existing text layer and the annotations of a page (#210).

A PDF that already has a text layer (OCR from another app) and highlights
goes through OCRmyPDF with the arguments `build_ocr_args` builds. With
`--skip-text` OCRmyPDF leaves that page alone, but its default PDF/A output
runs Ghostscript over the whole file: it dropped every annotation, and on a
real page (`BGB AT Fall 2 1/p001`) split the text layer into one font per
glyph. The stub's layer does not reproduce that split, so this test pins the
annotations and the text; the font split is measured on the real page.

The stub engine of test_hocr_text_layer_order.py stands in for recognition,
both for the fixture's text layer and in the Stage-1 call, so no real OCR
and no German language data are needed. Requirements as
there: ocrmypdf==17.8.0, the tesseract binary and pdftotext; missing ones
skip, unless REQUIRE_OCRMYPDF=1 (CI).

Run with: ~/.venvs/ocrmypdf/bin/python -m pytest bin/test/test_existing_text_layer.py -q
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent
STUB = BIN / "test" / "hocr_order_stub_engine.py"

PINNED_OCRMYPDF = "17.8.0"
STUB_ENV = dict(os.environ, HOCR_ORDER=json.dumps([["L", 0], ["L", 1], ["R", 0]]))

pytestmark = pytest.mark.slow  # runs the OCRmyPDF CLI; `make test-fast` skips it


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
             f"ocrmypdf {module.__version__} is not the pinned {PINNED_OCRMYPDF}")
    for tool in ("tesseract", "pdftotext"):
        _require(shutil.which(tool) is not None, f"{tool} is not on PATH")
    return module


def _ocrmypdf(*args, env=None):
    subprocess.run([sys.executable, "-m", "ocrmypdf", "-q", *args], check=True, env=env)


def _stage1_args():
    """The OCRmyPDF arguments of a Tesseract run, as build_ocr_args builds them."""
    script = ('source "$SCRIPT_DIR/pdf-lib.sh"; RESOLVED_ENGINE=tesseract; '
              'detect_optimizers; JOBS=1; build_ocr_args ocr_args; printf "%s\\n" "${ocr_args[@]}"')
    out = subprocess.run(["bash", "-c", script], env=dict(os.environ, SCRIPT_DIR=str(BIN)),
                         capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def _page_with_text_and_highlight(tmp_path):
    import pikepdf
    from PIL import Image

    scan = tmp_path / "scan.pdf"
    Image.new("RGB", (2480, 3508), "white").save(scan, "PDF", resolution=300)
    layered = tmp_path / "layered.pdf"
    _ocrmypdf("--plugin", str(STUB), "-l", "deu", "--force-ocr", "--output-type", "pdf",
              "--optimize", "0", str(scan), str(layered), env=STUB_ENV)
    source = tmp_path / "source.pdf"
    with pikepdf.open(layered) as pdf:
        highlight = pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Highlight,
                                       Rect=[50, 700, 300, 730], C=[1, 1, 0],
                                       QuadPoints=[50, 730, 300, 730, 50, 700, 300, 700])
        pdf.pages[0].obj.Annots = pdf.make_indirect(pikepdf.Array([pdf.make_indirect(highlight)]))
        pdf.save(source)
    return source


def _fingerprint(path):
    import pikepdf

    with pikepdf.open(path) as pdf:
        page = pdf.pages[0].obj
        annots = [str(a.Subtype) for a in page.get("/Annots", [])]
        fonts = len(page.Resources.get("/Font", {}))
    words = subprocess.run(["pdftotext", "-raw", str(path), "-"],
                           capture_output=True, text=True, check=True).stdout.split()
    return {"annots": annots, "fonts": fonts, "words": words}


def test_existing_text_layer_and_highlights_survive(tmp_path, ocrmypdf):
    source = _page_with_text_and_highlight(tmp_path)
    output = tmp_path / "ocr.pdf"

    _ocrmypdf("--plugin", str(STUB), *_stage1_args(), str(source), str(output), env=STUB_ENV)

    before = _fingerprint(source)
    assert before["annots"] == ["/Highlight"] and before["words"], before
    assert _fingerprint(output) == before

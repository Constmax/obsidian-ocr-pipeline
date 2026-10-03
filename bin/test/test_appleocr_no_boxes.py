"""The Apple Vision text layer draws no line boxes (issue #209).

ocrmypdf-appleocr 0.3.4 renders its own sandwich text layer and strokes a red
rectangle around every line. OCRmyPDF lays that layer under the scan, so the
boxes show only while a viewer is still decoding the image. Loaded next to
the engine, `bin/appleocr_no_boxes.py` keeps the text and drops the boxes.

Requirements: macOS with Apple Vision, ocrmypdf 17.8.0 with
ocrmypdf-appleocr 0.3.4, tesseract (OCRmyPDF checks for it) and pdftotext.
The test skips without them, also in CI: Linux has no Apple Vision.

Run with: ~/.venvs/ocrmypdf/bin/python -m pytest bin/test/test_appleocr_no_boxes.py -q
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SHIM = Path(__file__).resolve().parent.parent / "appleocr_no_boxes.py"
WORDS = ["Haftung", "des", "Verkaufers", "nach", "Gefahrubergang"]


@pytest.fixture(scope="module")
def ocrmypdf():
    if sys.platform != "darwin":
        pytest.skip("Apple Vision needs macOS")
    module = pytest.importorskip("ocrmypdf")
    pytest.importorskip("ocrmypdf_appleocr")
    if module.__version__ != "17.8.0":
        pytest.skip(f"ocrmypdf {module.__version__} is installed; pinned is 17.8.0")
    for tool in ("tesseract", "pdftotext"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not on PATH")
    return module


def _scan(path):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (2480, 3508), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=90)
    for row in range(3):
        draw.text((200, 300 + row * 200), " ".join(WORDS), fill="black", font=font)
    image.save(path, "PDF", resolution=300)


def _operators(content):
    """Operators of a page or form, including the forms it draws."""
    import pikepdf

    found = {str(op) for _, op in pikepdf.parse_content_stream(content)}
    for xobject in content.get("/Resources", {}).get("/XObject", {}).values():
        if xobject.get("/Subtype") == "/Form":
            found |= _operators(xobject)
    return found


def test_apple_text_layer_has_words_and_no_boxes(tmp_path, ocrmypdf):
    import pikepdf

    source, output = tmp_path / "scan.pdf", tmp_path / "ocr.pdf"
    _scan(source)

    # The CLI, as bin/ calls it: the Python API skips the plugins' option defaults.
    subprocess.run([sys.executable, "-m", "ocrmypdf", "-q",
                    "--plugin", "ocrmypdf_appleocr", "--plugin", str(SHIM),
                    "-l", "deu", "--output-type", "pdf", "--optimize", "0",
                    str(source), str(output)], check=True)

    with pikepdf.open(output) as pdf:
        operators = _operators(pdf.pages[0].obj)
    assert not operators & {"RG", "S"}, operators
    text = subprocess.run(["pdftotext", "-raw", str(output), "-"],
                          capture_output=True, text=True, check=True).stdout
    assert text.split() == WORDS * 3

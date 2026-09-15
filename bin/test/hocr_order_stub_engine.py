"""OCRmyPDF engine plugin used by test_hocr_text_layer_order.py.

Returns one fixed two-column hOCR page instead of recognizing anything. The
element order comes from the HOCR_ORDER environment variable, and all boxes
are scaled to the page image OCRmyPDF hands over, so the geometry always
matches the rasterized page.
"""
import json
import os

from ocrmypdf import OcrEngine, OrientationConfidence, hookimpl

REF_W, REF_H = 2480, 3508  # A4 at 300 dpi
COLUMNS = {"L": (200, 1150), "R": (1330, 2280)}
ROWS = (400, 520, 640)
LINE_HEIGHT = 60
WORDS = {
    "L": ("leftone", "lefttwo", "leftthree"),
    "R": ("rightone", "righttwo", "rightthree"),
}


def words(order):
    """Words in the element order given as (column, row) pairs."""
    return [WORDS[col][row] for col, row in order]


def build_hocr(order, width, height):
    """hOCR page whose element order is `order`, scaled to width x height."""
    sx, sy = width / REF_W, height / REF_H

    def bbox(x0, y0, x1, y1):
        return (f"bbox {round(x0 * sx)} {round(y0 * sy)} "
                f"{round(x1 * sx)} {round(y1 * sy)}")

    paragraphs = []
    for col, row in order:
        x0, x1 = COLUMNS[col]
        y0, y1 = ROWS[row], ROWS[row] + LINE_HEIGHT
        paragraphs.append(
            f'<p class="ocr_par" title="{bbox(x0, y0, x1, y1)}">'
            f'<span class="ocr_line" title="{bbox(x0, y0, x1, y1)}; baseline 0 -12">'
            f'<span class="ocrx_word" title="{bbox(x0, y0, x0 + 500, y1)}; x_wconf 95">'
            f'{WORDS[col][row]}</span></span></p>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title></title></head><body>'
            f'<div class="ocr_page" title="bbox 0 0 {width} {height}">'
            f'{"".join(paragraphs)}</div></body></html>')


class OrderStubEngine(OcrEngine):
    @staticmethod
    def version():
        return "0"

    @staticmethod
    def creator_tag(options):
        return "hocr-order-stub"

    def __str__(self):
        return "hocr-order-stub"

    @staticmethod
    def languages(options):
        return {"deu"}

    @staticmethod
    def get_orientation(input_file, options):
        return OrientationConfidence(angle=0, confidence=0.0)

    @staticmethod
    def get_deskew(input_file, options):
        return 0.0

    @staticmethod
    def generate_hocr(input_file, output_hocr, output_text, options):
        from PIL import Image

        order = json.loads(os.environ["HOCR_ORDER"])
        with Image.open(input_file) as image:
            width, height = image.size
        output_hocr.write_text(build_hocr(order, width, height), encoding="utf-8")
        output_text.write_text(" ".join(words(order)), encoding="utf-8")

    @staticmethod
    def generate_pdf(input_file, output_pdf, output_text, options):
        raise NotImplementedError("only the hOCR renderer path is exercised")


@hookimpl
def get_ocr_engine(options):
    return OrderStubEngine()

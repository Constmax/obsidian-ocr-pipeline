"""OCRmyPDF engine plugin: PaddleOCR PP-OCRv5 models through RapidOCR.

    ocrmypdf --plugin ocrmypdf_paddle -l deu input.pdf output.pdf

Plan: docs/paddle-textlayer.md, step 2 (#68). Lines keep RapidOCR's
top-to-bottom order until the reading-order step (#69); multi-column pages
need split-column processing until then.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from pathlib import Path

from ocrmypdf import OcrEngine, hookimpl
from ocrmypdf._exec import tesseract
from ocrmypdf.exceptions import BadArgsError, MissingDependencyError

from ocrmypdf_paddle import runtime
from ocrmypdf_paddle.hocr import render_page

__version__ = "0.1.0"

log = logging.getLogger(__name__)

#: Directory for one JSON file per page (recognized lines, polygons, scores),
#: used to check that selection lines up with the page image.
DEBUG_DIR_ENV = "OCRMYPDF_PADDLE_DEBUG_DIR"

_recognizer = runtime.Recognizer()


def _selected(options) -> bool:
    # OCRmyPDF 17.8.0 accepts only auto, tesseract and none for --ocr-engine.
    # Loading this plugin makes it the engine behind "auto", as with the
    # Apple OCR plugin; an explicit tesseract or none leaves it inactive.
    return options is None or getattr(options, "ocr_engine", "auto") == "auto"


@hookimpl
def check_options(options):
    if not _selected(options):
        return
    try:
        runtime.recognition_language(options.languages)
    except ValueError as error:
        raise BadArgsError(f"PaddleOCR engine {error}.") from error
    if options.pdf_renderer == "sandwich":
        raise BadArgsError(
            "PaddleOCR engine does not support --pdf-renderer sandwich: it produces "
            "hOCR for the fpdf2 renderer. Use --pdf-renderer auto or fpdf2."
        )
    if options.jobs is not None and options.jobs > 1:
        log.warning(
            "PaddleOCR engine runs one OCR job; ignoring --jobs %d.", options.jobs
        )
    options.jobs = 1
    problems = runtime.missing_runtime() + runtime.check_models(runtime.model_dir())
    if problems:
        raise MissingDependencyError(
            "PaddleOCR engine is not ready:\n  " + "\n  ".join(problems)
            + f"\nModel files are read from {runtime.MODEL_DIR_ENV} "
            f"(default {runtime.model_dir()})."
        )


class PaddleOcrEngine(OcrEngine):
    """PP-OCRv5 mobile detection and Latin recognition via RapidOCR."""

    @staticmethod
    def version():
        return __version__

    @staticmethod
    def creator_tag(options):
        rapidocr = runtime.PINNED_PACKAGES["rapidocr"]
        return f"ocrmypdf-paddle {__version__} (RapidOCR {rapidocr}, PP-OCRv5 mobile)"

    def __str__(self):
        return f"PaddleOCR PP-OCRv5 via RapidOCR (ocrmypdf-paddle {__version__})"

    @staticmethod
    def languages(options):
        return set(runtime.LANGUAGES)

    # Orientation and deskew come from Tesseract for now: OCRmyPDF 17.8.0
    # requires the tesseract binary with any engine plugin anyway
    # (docs/paddle-textlayer.md, step 0).

    @staticmethod
    def get_orientation(input_file, options):
        return tesseract.get_orientation(
            input_file,
            engine_mode=options.tesseract.oem,
            timeout=options.tesseract.non_ocr_timeout,
            omp_thread_limit=options.tesseract.omp_thread_limit,
        )

    @staticmethod
    def get_deskew(input_file, options):
        return tesseract.get_deskew(
            input_file,
            languages=options.languages,
            engine_mode=options.tesseract.oem,
            timeout=options.tesseract.non_ocr_timeout,
            omp_thread_limit=options.tesseract.omp_thread_limit,
        )

    @staticmethod
    def generate_hocr(input_file, output_hocr, output_text, options):
        input_file = Path(input_file)
        page = _recognizer.recognize(input_file)
        hocr, text = render_page(page.lines, page.width, page.height)
        Path(output_hocr).write_text(hocr, encoding="utf-8")
        Path(output_text).write_text(text, encoding="utf-8")
        _write_debug(input_file, page)

    @staticmethod
    def generate_pdf(input_file, output_pdf, output_text, options):
        raise NotImplementedError(
            "check_options rejects --pdf-renderer sandwich, so OCRmyPDF never asks "
            "this engine for a text-only PDF"
        )


def _write_debug(input_file: Path, page: runtime.RecognizedPage) -> None:
    directory = os.environ.get(DEBUG_DIR_ENV)
    if not directory:
        return
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    record = {
        "image": input_file.name,
        "width": page.width,
        "height": page.height,
        "lines": [asdict(line) for line in page.lines],
    }
    (target / f"{input_file.stem}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8"
    )


@hookimpl
def get_ocr_engine(options):
    if not _selected(options):
        return None
    return PaddleOcrEngine()

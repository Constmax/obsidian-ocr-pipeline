"""The PaddleOCR engine inside the pinned OCRmyPDF, with a fake recognizer.

Recognition is replaced; option checks, engine selection, hOCR parsing, fpdf2
rendering and grafting are OCRmyPDF's own. No RapidOCR or model weights are
needed. Requirements and skip policy follow
bin/test/test_hocr_text_layer_order.py: ocrmypdf==17.8.0, pdftotext, and for
the pipeline tests the tesseract binary; REQUIRE_OCRMYPDF=1 (set in CI) turns
every skip into a failure.
"""
import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

PINNED_OCRMYPDF = "17.8.0"
REF_W, REF_H = 2480, 3508  # A4 at 300 dpi


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
             f"ocrmypdf {module.__version__} is installed; these tests pin {PINNED_OCRMYPDF}")
    return module


@pytest.fixture(scope="module")
def plugin(ocrmypdf):
    import ocrmypdf_paddle

    return ocrmypdf_paddle


@pytest.fixture(scope="module")
def pdftotext():
    path = shutil.which("pdftotext")
    _require(path is not None, "pdftotext (poppler) is not on PATH")
    return path


@pytest.fixture
def tesseract_binary():
    _require(shutil.which("tesseract") is not None,
             "tesseract is not on PATH; OCRmyPDF 17.8.0 requires it with any engine plugin")


def raw_words(pdftotext, pdf):
    out = subprocess.run([pdftotext, "-raw", str(pdf), "-"], capture_output=True, text=True,
                         check=True)
    return out.stdout.split()


def rect(x0, y0, x1, y1):
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


# Two columns emitted right column first: the text layer must follow this
# order, not the geometry (the #61 mechanism, applied to this engine's hOCR).
PAGE_LINES = [
    ("rechts oben", rect(1330, 400, 2280, 460)),
    ("rechts unten", rect(1330, 520, 2280, 580)),
    ("links oben", rect(200, 400, 1150, 460)),
    ("links unten", rect(200, 520, 1150, 580)),
]


def page_lines(plugin, width=REF_W, height=REF_H, specs=PAGE_LINES):
    from ocrmypdf_paddle.hocr import TextLine

    sx, sy = width / REF_W, height / REF_H
    return [TextLine(text, tuple((x * sx, y * sy) for x, y in polygon), 0.9)
            for text, polygon in specs]


def expected_words():
    return [word for text, _ in PAGE_LINES for word in text.split()]


# What the engine sees from RapidOCR: a two-column page sorted by y, so the
# columns alternate. The text layer must read the left column, then the right.
COLUMN_LINES = [
    (f"{side} {i}", rect(x0, 400 + i * 120, x1, 460 + i * 120))
    for i in range(5)
    for side, x0, x1 in (("links", 200, 1150), ("rechts", 1330, 2280))
]
COLUMN_ORDER = [i for side in (0, 1) for i in range(side, len(COLUMN_LINES), 2)]


def expected_column_words():
    return [word for i in COLUMN_ORDER for word in COLUMN_LINES[i][0].split()]


class FakeRecognizer:
    def __init__(self, plugin):
        self.plugin = plugin
        self.threads = set()

    def recognize(self, image_path):
        from PIL import Image

        from ocrmypdf_paddle import runtime

        self.threads.add(threading.current_thread().name)
        with Image.open(image_path) as image:
            width, height = image.size
        return runtime.RecognizedPage(
            width, height, page_lines(self.plugin, width, height, COLUMN_LINES))


@pytest.fixture
def ready(monkeypatch, plugin):
    """Runtime and model checks pass; recognition is the fake."""
    monkeypatch.setattr(plugin.runtime, "missing_runtime", lambda: [])
    monkeypatch.setattr(plugin.runtime, "check_models", lambda directory: [])
    fake = FakeRecognizer(plugin)
    monkeypatch.setattr(plugin, "_recognizer", fake)
    return fake


def blank_pdf(path, pages=1):
    from PIL import Image

    images = [Image.new("RGB", (REF_W, REF_H), "white") for _ in range(pages)]
    images[0].save(path, "PDF", resolution=300, save_all=True, append_images=images[1:])
    return path


def run_ocr(ocrmypdf, source, output, **overrides):
    kwargs = dict(plugins=["ocrmypdf_paddle"], language=["deu"], force_ocr=True,
                  output_type="pdf", optimize=0, rasterizer="pypdfium", progress_bar=False)
    kwargs.update(overrides)
    return ocrmypdf.ocr(source, output, **kwargs)


# ── Rendering through OCRmyPDF's hOCR parser and fpdf2 renderer ────────────


def test_engine_hocr_parses_and_renders_in_line_order(tmp_path, ocrmypdf, plugin, pdftotext):
    from ocrmypdf.font import MultiFontManager
    from ocrmypdf.fpdf_renderer import Fpdf2PdfRenderer
    from ocrmypdf.hocrtransform.hocr_parser import HocrParser

    from ocrmypdf_paddle.hocr import TextLine, render_page

    skewed = TextLine("schief gedruckt", ((200, 700), (1150, 766), (1146, 826), (196, 760)),
                      0.87)
    hocr_text, text = render_page(page_lines(plugin) + [skewed], REF_W, REF_H)
    hocr = tmp_path / "page.hocr"
    hocr.write_text(hocr_text, encoding="utf-8")
    page = HocrParser(hocr).parse()

    def walk(element):
        yield element
        for child in element.children:
            yield from walk(child)

    parsed = [e for e in walk(page) if e.baseline is not None]
    assert [" ".join(w.text for w in line.children) for line in parsed] == text.splitlines()
    assert parsed[-1].baseline.slope == pytest.approx(66 / 950, abs=1e-6)
    assert parsed[-1].children[0].confidence == pytest.approx(0.87)

    pdf = tmp_path / "page.pdf"
    fonts = MultiFontManager(Path(ocrmypdf.__file__).parent / "data")
    Fpdf2PdfRenderer(page=page, dpi=300, multi_font_manager=fonts,
                     invisible_text=True).render(pdf)
    assert raw_words(pdftotext, pdf) == expected_words() + ["schief", "gedruckt"]


# ── Full OCRmyPDF pipeline ─────────────────────────────────────────────────


def test_pipeline_uses_the_engine_with_one_job(tmp_path, monkeypatch, caplog, ocrmypdf,
                                               plugin, ready, pdftotext, tesseract_binary):
    seen_jobs = []
    original = plugin.PaddleOcrEngine.generate_hocr

    def spy(input_file, output_hocr, output_text, options):
        seen_jobs.append(options.jobs)
        return original(input_file, output_hocr, output_text, options)

    monkeypatch.setattr(plugin.PaddleOcrEngine, "generate_hocr", staticmethod(spy))
    output = tmp_path / "ocr.pdf"
    with caplog.at_level(logging.WARNING):
        exit_code = run_ocr(ocrmypdf, blank_pdf(tmp_path / "blank.pdf", pages=2), output, jobs=4)

    assert exit_code == 0
    assert seen_jobs == [1, 1]
    assert "PaddleOCR engine runs one OCR job; ignoring --jobs 4." in caplog.text
    assert raw_words(pdftotext, output) == expected_column_words() * 2


def test_sandwich_renderer_is_rejected(tmp_path, ocrmypdf, plugin, ready):
    from ocrmypdf.exceptions import BadArgsError

    with pytest.raises(BadArgsError, match="does not support --pdf-renderer sandwich"):
        run_ocr(ocrmypdf, blank_pdf(tmp_path / "blank.pdf"), tmp_path / "ocr.pdf",
                pdf_renderer="sandwich")


def test_unsupported_language_is_rejected(tmp_path, ocrmypdf, plugin, ready):
    from ocrmypdf.exceptions import BadArgsError

    with pytest.raises(BadArgsError, match=r"supports only -l deu, not -l deu\+eng"):
        run_ocr(ocrmypdf, blank_pdf(tmp_path / "blank.pdf"), tmp_path / "ocr.pdf",
                language=["deu", "eng"])


# ── Hooks and engine without the pipeline ──────────────────────────────────


def options(**overrides):
    values = dict(ocr_engine="auto", languages=["deu"], pdf_renderer="auto", jobs=None,
                  tesseract=SimpleNamespace(oem=None, non_ocr_timeout=180.0,
                                            omp_thread_limit=1))
    values.update(overrides)
    return SimpleNamespace(**values)


def test_missing_runtime_or_models_stop_before_any_page(monkeypatch, plugin):
    from ocrmypdf.exceptions import MissingDependencyError

    monkeypatch.setattr(plugin.runtime, "missing_runtime",
                        lambda: ["rapidocr==3.9.2 is not installed"])
    monkeypatch.setattr(plugin.runtime, "check_models",
                        lambda directory: [f"model file missing: {directory}/x.onnx"])
    monkeypatch.setenv(plugin.runtime.MODEL_DIR_ENV, "/models")

    with pytest.raises(MissingDependencyError) as error:
        plugin.check_options(options())
    assert str(error.value).splitlines() == [
        "PaddleOCR engine is not ready:",
        "  rapidocr==3.9.2 is not installed",
        "  model file missing: /models/x.onnx",
        "Model files are read from OCRMYPDF_PADDLE_MODEL_DIR (default /models).",
    ]


def test_default_jobs_become_one_without_a_warning(plugin, ready, caplog):
    opts = options(jobs=None)
    with caplog.at_level(logging.WARNING):
        plugin.check_options(opts)
    assert opts.jobs == 1
    assert caplog.text == ""


@pytest.mark.parametrize("engine", ["tesseract", "none"])
def test_an_explicit_other_engine_leaves_the_plugin_inactive(plugin, engine):
    opts = options(ocr_engine=engine, languages=["eng"], pdf_renderer="sandwich", jobs=8)
    plugin.check_options(opts)  # no language, renderer or dependency checks
    assert opts.jobs == 8
    assert plugin.get_ocr_engine(opts) is None
    assert isinstance(plugin.get_ocr_engine(options()), plugin.PaddleOcrEngine)


def test_orientation_and_deskew_come_from_tesseract(monkeypatch, plugin):
    calls = []
    monkeypatch.setattr(plugin.tesseract, "get_orientation",
                        lambda *args, **kwargs: calls.append(("orientation", args, kwargs)))
    monkeypatch.setattr(plugin.tesseract, "get_deskew",
                        lambda *args, **kwargs: calls.append(("deskew", args, kwargs)))
    engine = plugin.PaddleOcrEngine()
    engine.get_orientation(Path("p.png"), options())
    engine.get_deskew(Path("p.png"), options())

    assert calls == [
        ("orientation", (Path("p.png"),),
         dict(engine_mode=None, timeout=180.0, omp_thread_limit=1)),
        ("deskew", (Path("p.png"),),
         dict(languages=["deu"], engine_mode=None, timeout=180.0, omp_thread_limit=1)),
    ]


def test_sandwich_path_is_explicitly_unreachable(tmp_path, plugin):
    with pytest.raises(NotImplementedError, match="sandwich"):
        plugin.PaddleOcrEngine.generate_pdf(tmp_path / "p.png", tmp_path / "p.pdf",
                                            tmp_path / "p.txt", options())


def test_debug_artifact_records_the_recognized_lines(tmp_path, monkeypatch, plugin, ready):
    from PIL import Image

    image = tmp_path / "000001_ocr.png"
    Image.new("L", (1240, 1754), 255).save(image)
    monkeypatch.setenv(plugin.DEBUG_DIR_ENV, str(tmp_path / "debug"))
    plugin.PaddleOcrEngine.generate_hocr(image, tmp_path / "p.hocr", tmp_path / "p.txt",
                                         options())

    record = json.loads((tmp_path / "debug" / "000001_ocr.json").read_text(encoding="utf-8"))
    assert (record["width"], record["height"]) == (1240, 1754)
    assert [line["text"] for line in record["lines"]] == [text for text, _ in COLUMN_LINES]
    assert record["lines"][0]["polygon"][0] == [100.0, 200.0]
    assert record["order"] == COLUMN_ORDER
    assert (tmp_path / "p.txt").read_text(encoding="utf-8").splitlines() == [
        COLUMN_LINES[i][0] for i in COLUMN_ORDER]

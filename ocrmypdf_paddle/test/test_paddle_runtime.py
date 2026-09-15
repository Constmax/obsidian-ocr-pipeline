"""RapidOCR runtime wrapper: configuration, result parsing, locking."""
import hashlib
import importlib.metadata
import logging
import math
import re
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

from ocrmypdf_paddle import runtime
from ocrmypdf_paddle.hocr import TextLine, Word


class EngineType(Enum):
    ONNXRUNTIME = "onnxruntime"


class LangDet(Enum):
    CH = "ch"


class LangRec(Enum):
    CH = "ch"
    LATIN = "latin"


class ModelType(Enum):
    MOBILE = "mobile"
    SERVER = "server"


class OCRVersion(Enum):
    PPOCRV5 = "PP-OCRv5"


FAKE_API = SimpleNamespace(EngineType=EngineType, LangDet=LangDet, LangRec=LangRec,
                           ModelType=ModelType, OCRVersion=OCRVersion)


def rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def result(**fields):
    return SimpleNamespace(**fields)


def test_ml_runtime_is_not_imported_with_the_plugin():
    assert "rapidocr" not in sys.modules
    assert "onnxruntime" not in sys.modules


# ── Language and configuration ─────────────────────────────────────────────


def test_german_maps_to_the_latin_recognizer():
    assert runtime.recognition_language(["deu"]) == "latin"
    assert runtime.recognition_language(["deu", "deu"]) == "latin"


@pytest.mark.parametrize("languages, given", [(["eng"], "eng"), (["deu", "eng"], "deu+eng"),
                                               ([], "(none)")])
def test_other_languages_are_rejected_with_the_supported_one(languages, given):
    with pytest.raises(ValueError, match=rf"^supports only -l deu, not -l {re.escape(given)}$"):
        runtime.recognition_language(languages)


def test_rapidocr_params_are_the_spike_configuration(tmp_path):
    params = runtime.rapidocr_params(tmp_path, "latin", FAKE_API)

    assert params["Global.model_root_dir"] == str(tmp_path)
    assert params["EngineConfig.onnxruntime.intra_op_num_threads"] == 4
    assert params["EngineConfig.onnxruntime.inter_op_num_threads"] == 4
    assert params["Global.max_side_len"] >= 3508  # A4 at 300 dpi keeps full resolution
    assert params["Global.text_score"] == 0.0     # no cutoff before calibration
    assert params["Global.use_cls"] is False
    assert params["Global.return_word_box"] is True
    assert (params["Det.model_type"], params["Det.ocr_version"]) == (ModelType.MOBILE,
                                                                     OCRVersion.PPOCRV5)
    assert (params["Rec.lang_type"], params["Rec.model_type"]) == (LangRec.LATIN,
                                                                   ModelType.MOBILE)


def test_model_dir_comes_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(runtime.MODEL_DIR_ENV, str(tmp_path / "models"))
    assert runtime.model_dir() == tmp_path / "models"
    monkeypatch.delenv(runtime.MODEL_DIR_ENV)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert runtime.model_dir() == tmp_path / ".cache" / "ocrmypdf-paddle" / "models"


def test_check_models_names_missing_and_mismatching_files(monkeypatch, tmp_path):
    good, bad = b"model a", b"model b"
    monkeypatch.setattr(runtime, "MODEL_FILES", (
        runtime.ModelFile("a.onnx", hashlib.sha256(good).hexdigest()),
        runtime.ModelFile("b.onnx", hashlib.sha256(bad).hexdigest()),
        runtime.ModelFile("c.onnx", "0" * 64),
    ))
    (tmp_path / "a.onnx").write_bytes(good)
    (tmp_path / "b.onnx").write_bytes(b"tampered")

    assert runtime.check_models(tmp_path) == [
        f"model file does not match the pinned SHA-256: {tmp_path / 'b.onnx'}",
        f"model file missing: {tmp_path / 'c.onnx'}",
    ]
    (tmp_path / "b.onnx").write_bytes(bad)
    monkeypatch.setattr(runtime, "MODEL_FILES", runtime.MODEL_FILES[:2])
    assert runtime.check_models(tmp_path) == []


def test_missing_runtime_reports_absent_and_unpinned_packages(monkeypatch):
    installed = {"onnxruntime": "1.27.0"}

    def version(package):
        if package not in installed:
            raise importlib.metadata.PackageNotFoundError(package)
        return installed[package]

    monkeypatch.setattr(runtime.importlib.metadata, "version", version)
    assert runtime.missing_runtime() == [
        "rapidocr==3.9.2 is not installed",
        "onnxruntime 1.27.0 is installed; this engine is pinned to 1.26.0",
    ]
    installed.update(rapidocr="3.9.2", onnxruntime="1.26.0")
    assert runtime.missing_runtime() == []


# ── Result parsing ─────────────────────────────────────────────────────────


def test_lines_keep_recognizer_order_scores_and_word_pieces():
    lines = runtime.lines_from_result(result(
        boxes=[rect(10, 50, 200, 80), rect(10, 10, 200, 40)],
        txts=("unten", "oben"),
        scores=(0.5, 0.9),
        word_results=((("unten", 0.5, rect(10, 50, 90, 80)),),
                      (("oben", 0.9, rect(10, 10, 80, 40)),)),
    ))

    assert [line.text for line in lines] == ["unten", "oben"]
    assert lines[1] == TextLine("oben", ((10.0, 10.0), (200.0, 10.0), (200.0, 40.0), (10.0, 40.0)),
                                0.9, (Word("oben", ((10.0, 10.0), (80.0, 10.0), (80.0, 40.0),
                                                    (10.0, 40.0)), 0.9),))


def test_coordinates_are_mapped_back_from_a_downscaled_image():
    (line,) = runtime.lines_from_result(
        result(boxes=[rect(100, 40, 200, 80)], txts=("a",), scores=(1,),
               word_results=((("a", 1, rect(100, 40, 150, 80)),),)),
        scale_x=0.5, scale_y=0.25)

    assert line.polygon == ((200.0, 160.0), (400.0, 160.0), (400.0, 320.0), (200.0, 320.0))
    assert line.words[0].polygon[1] == (300.0, 160.0)


@pytest.mark.parametrize("empty", [result(), result(boxes=None, txts=None, scores=None),
                                    result(boxes=[], txts=(), scores=(), word_results=())])
def test_an_empty_result_has_no_lines(empty):
    assert runtime.lines_from_result(empty) == []


@pytest.mark.parametrize("fields", [
    dict(boxes=[rect(0, 0, 10, 10)], txts=("a", "b"), scores=(1, 1)),
    dict(boxes=[rect(0, 0, 10, 10), rect(0, 20, 10, 30)], txts=("a", "b"), scores=(1,)),
    dict(boxes=None, txts=("a",), scores=(1,)),
])
def test_boxes_texts_and_scores_that_do_not_line_up_are_rejected(fields):
    with pytest.raises(runtime.MalformedResultError, match="boxes"):
        runtime.lines_from_result(result(**fields))


def test_word_groups_are_dropped_when_not_one_per_line():
    lines = runtime.lines_from_result(result(
        boxes=[rect(0, 0, 100, 10), rect(0, 20, 100, 30)], txts=("a", "b"), scores=(1, 1),
        word_results=((("b", 1, rect(0, 20, 10, 30)),),),  # RapidOCR dropped line a's group
    ))
    assert [line.words for line in lines] == [(), ()]


@pytest.mark.parametrize("group", [
    (("a", 1),),                          # no box
    (("a", 1, None),),                    # box missing
    ((7, 1, rect(0, 0, 10, 10)),),        # text is not a string
    "abc",                                # not a group at all
    None,
    SimpleNamespace(words=["a"]),         # RapidOCR's raw WordInfo without boxes
])
def test_malformed_word_groups_give_no_pieces(group):
    (line,) = runtime.lines_from_result(result(
        boxes=[rect(0, 0, 100, 10)], txts=("a",), scores=(1,), word_results=(group,)))
    assert line.words == ()
    assert line.text == "a"


def test_malformed_values_become_unplaceable_instead_of_raising():
    (line,) = runtime.lines_from_result(result(boxes=["junk"], txts=(None,), scores=("x",)))
    assert line.polygon == ()
    assert line.text == ""
    assert math.isnan(line.confidence)


def test_downscale_factor_only_shrinks_oversized_images():
    assert runtime.downscale_factor(2480, 3508) == 1.0
    assert runtime.downscale_factor(4000, 100) == 1.0
    assert runtime.downscale_factor(16144, 22828) == pytest.approx(4000 / 22828)


# ── Recognizer ─────────────────────────────────────────────────────────────


class FakeEngine:
    def __init__(self, fail_with_word_boxes=False, delay=0.0):
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.fail_with_word_boxes = fail_with_word_boxes
        self.delay = delay
        self._count = threading.Lock()

    def __call__(self, source, return_word_box):
        with self._count:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            self.calls.append((source, return_word_box))
            if return_word_box and self.fail_with_word_boxes:
                raise IndexError("tuple index out of range")
            return result(boxes=[rect(400, 40, 800, 80)], txts=("Text",), scores=(0.9,))
        finally:
            with self._count:
                self.active -= 1


@pytest.fixture
def page_image(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / "page.png"
    Image.new("L", (2480, 3508), 255).save(path)
    return path


def test_the_pipeline_is_created_on_first_use_and_reused(page_image):
    engines = []

    def factory():
        engines.append(FakeEngine())
        return engines[-1]

    recognizer = runtime.Recognizer(factory)
    assert engines == []
    first = recognizer.recognize(page_image)
    recognizer.recognize(page_image)

    assert len(engines) == 1 and len(engines[0].calls) == 2
    assert engines[0].calls[0] == (str(page_image), True)
    assert (first.width, first.height, [line.text for line in first.lines]) == (2480, 3508,
                                                                                ["Text"])


def test_recognition_calls_never_overlap(page_image):
    engine = FakeEngine(delay=0.05)
    created = []
    recognizer = runtime.Recognizer(lambda: created.append(1) or engine)
    threads = [threading.Thread(target=recognizer.recognize, args=(page_image,))
               for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(engine.calls) == 4
    assert engine.max_active == 1
    assert created == [1]


def test_word_box_failure_retries_without_word_boxes(page_image, caplog):
    engine = FakeEngine(fail_with_word_boxes=True)
    with caplog.at_level(logging.WARNING, logger=runtime.__name__):
        page = runtime.Recognizer(lambda: engine).recognize(page_image)

    assert [flag for _, flag in engine.calls] == [True, False]
    assert [line.text for line in page.lines] == ["Text"]
    assert "spreading words evenly" in caplog.text


def test_oversized_images_are_downscaled_and_mapped_back(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / "huge.png"
    Image.new("1", (8000, 1000), 1).save(path)
    engine = FakeEngine()
    page = runtime.Recognizer(lambda: engine).recognize(path)

    source, _ = engine.calls[0]
    assert source.size == (4000, 500)
    assert (page.width, page.height) == (8000, 1000)
    assert page.lines[0].polygon == ((800.0, 80.0), (1600.0, 80.0), (1600.0, 160.0),
                                     (800.0, 160.0))

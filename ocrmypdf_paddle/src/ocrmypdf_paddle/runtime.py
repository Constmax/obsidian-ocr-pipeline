"""RapidOCR (ONNX Runtime) with PaddleOCR PP-OCRv5 models, behind one lock.

RapidOCR, ONNX Runtime and Pillow are imported only when a page is
recognized, so the helpers here are testable without them. The configuration
is the one accepted in the runtime spike (#62, bench/ERGEBNIS.md,
Nachtrag 18).
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import logging
import math
import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ocrmypdf_paddle.hocr import TextLine, Word

log = logging.getLogger(__name__)

PINNED_PACKAGES = {"rapidocr": "3.9.2", "onnxruntime": "1.26.0"}

#: OCRmyPDF language code -> RapidOCR recognition language. German is read by
#: the Latin PP-OCRv5 recognizer; RapidOCR has no separate German model.
LANGUAGES = {"deu": "latin"}

#: ONNX Runtime intra- and inter-op threads. The spike rejected one thread
#: (dense two-column pages took up to 16.2 s) and accepted four (at most
#: 10.5 s). OCRmyPDF still runs one job, so calls stay sequential.
ORT_THREADS = 4

#: Longest image side handed to RapidOCR. At least an A4 page at 300 dpi
#: (3508 px), so ordinary pages keep full resolution; larger rasters (the
#: spike saw 368 MP from --redo-ocr) are downscaled before recognition.
MAX_SIDE_PX = 4000

MODEL_DIR_ENV = "OCRMYPDF_PADDLE_MODEL_DIR"


@dataclass(frozen=True)
class ModelFile:
    name: str
    sha256: str


#: Model files of RapidOCR 3.9.2 as recorded in the spike. RapidOCR loads the
#: text-line classifier even with use_cls off, so it is required too.
MODEL_FILES = (
    ModelFile(
        "ch_PP-OCRv5_det_mobile.onnx",
        "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
    ),
    ModelFile(
        "latin_PP-OCRv5_rec_mobile.onnx",
        "b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a",
    ),
    ModelFile(
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
    ),
)


class MalformedResultError(RuntimeError):
    """RapidOCR returned boxes, texts and scores that do not line up."""


def recognition_language(languages: Sequence[str]) -> str:
    """RapidOCR recognition language for OCRmyPDF's language list.

    Raises ValueError, phrased to follow "PaddleOCR engine", for anything but
    exactly one supported language.
    """
    requested = sorted(set(languages))
    if len(requested) != 1 or requested[0] not in LANGUAGES:
        given = "+".join(languages) or "(none)"
        return_value = "/".join(sorted(LANGUAGES))
        raise ValueError(f"supports only -l {return_value}, not -l {given}")
    return LANGUAGES[requested[0]]


def model_dir() -> Path:
    configured = os.environ.get(MODEL_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "ocrmypdf-paddle" / "models"


def missing_runtime() -> list[str]:
    """Problems with the installed RapidOCR and ONNX Runtime; empty when pinned."""
    problems = []
    for package, pinned in PINNED_PACKAGES.items():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            problems.append(f"{package}=={pinned} is not installed")
            continue
        if installed != pinned:
            problems.append(f"{package} {installed} is installed; this engine is pinned to {pinned}")
    return problems


def check_models(directory: Path) -> list[str]:
    """Problems with the model files in `directory`; empty when all match.

    RapidOCR silently downloads a missing or mismatching model. That would
    turn an OCR run into a hidden network fetch, so the engine refuses
    instead.
    """
    problems = []
    for model in MODEL_FILES:
        path = directory / model.name
        if not path.is_file():
            problems.append(f"model file missing: {path}")
        elif _sha256(path) != model.sha256:
            problems.append(f"model file does not match the pinned SHA-256: {path}")
    return problems


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rapidocr_params(directory: Path, language: str, api: Any) -> dict[str, Any]:
    """RapidOCR constructor parameters. `api` is the rapidocr module (enums)."""
    return {
        "Global.model_root_dir": str(directory),
        "Global.log_level": "warning",
        "Global.max_side_len": MAX_SIDE_PX,
        # OCRmyPDF handles page rotation (Tesseract OSD).
        "Global.use_cls": False,
        "Global.return_word_box": True,
        # Keep every line; no score cutoff before calibration (#71).
        "Global.text_score": 0.0,
        "EngineConfig.onnxruntime.intra_op_num_threads": ORT_THREADS,
        "EngineConfig.onnxruntime.inter_op_num_threads": ORT_THREADS,
        "Det.engine_type": api.EngineType.ONNXRUNTIME,
        # The PP-OCRv5 detector is multilingual; RapidOCR files it under "ch".
        "Det.lang_type": api.LangDet.CH,
        "Det.model_type": api.ModelType.MOBILE,
        "Det.ocr_version": api.OCRVersion.PPOCRV5,
        "Rec.engine_type": api.EngineType.ONNXRUNTIME,
        "Rec.lang_type": api.LangRec(language),
        "Rec.model_type": api.ModelType.MOBILE,
        "Rec.ocr_version": api.OCRVersion.PPOCRV5,
    }


def create_rapidocr() -> Any:
    directory = model_dir()
    problems = check_models(directory)
    if problems:
        raise RuntimeError("PaddleOCR models are not ready: " + "; ".join(problems))
    import rapidocr

    return rapidocr.RapidOCR(params=rapidocr_params(directory, LANGUAGES["deu"], rapidocr))


def downscale_factor(width: int, height: int, max_side: int = MAX_SIDE_PX) -> float:
    longest = max(width, height)
    return 1.0 if longest <= max_side else max_side / longest


def lines_from_result(result: Any, scale_x: float = 1.0, scale_y: float = 1.0) -> list[TextLine]:
    """TextLines in recognizer order, in the coordinates of the original image.

    `scale_x`/`scale_y` are the downscale factors applied before recognition.
    Lines need a box, a text and a score at the same index; if those lists
    differ in length, which text belongs to which box is unknown and the
    result is rejected. Individual unusable boxes are passed on and dropped
    during hOCR conversion.
    """
    boxes = _as_list(getattr(result, "boxes", None))
    txts = _as_list(getattr(result, "txts", None))
    scores = _as_list(getattr(result, "scores", None))
    if not len(boxes) == len(txts) == len(scores):
        raise MalformedResultError(
            f"RapidOCR returned {len(boxes)} boxes, {len(txts)} texts and {len(scores)} scores"
        )
    groups = _as_list(getattr(result, "word_results", None))
    # RapidOCR 3.9.2 drops the word group of a line without word boxes, so
    # groups are attached only when every line has exactly one.
    use_groups = len(groups) == len(txts)
    return [
        TextLine(
            text=text if isinstance(text, str) else "",
            polygon=_scaled(box, scale_x, scale_y),
            confidence=_score(score),
            words=_words(groups[i], scale_x, scale_y) if use_groups else (),
        )
        for i, (box, text, score) in enumerate(zip(boxes, txts, scores))
    ]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _score(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _scaled(points: Any, scale_x: float, scale_y: float) -> tuple[tuple[float, float], ...]:
    try:
        return tuple((float(x) / scale_x, float(y) / scale_y) for x, y in points)
    except (TypeError, ValueError):
        return ()


def _words(group: Any, scale_x: float, scale_y: float) -> tuple[Word, ...]:
    """Word pieces of one line; empty if any piece is not (text, score, box)."""
    words = []
    for item in _as_list(group):
        try:
            text, score, polygon = item
        except (TypeError, ValueError):
            return ()
        if not isinstance(text, str) or polygon is None:
            return ()
        words.append(Word(text, _scaled(polygon, scale_x, scale_y), _score(score)))
    return tuple(words)


@dataclass(frozen=True)
class RecognizedPage:
    width: int
    height: int
    lines: list[TextLine]


class Recognizer:
    """One RapidOCR pipeline per process, created on first use.

    A lock serializes creation and every recognition call. OCRmyPDF runs this
    engine with one job, but the pinned runtime is not proven thread-safe, so
    the lock also holds if that policy is bypassed.
    """

    def __init__(self, factory: Callable[[], Any] | None = None) -> None:
        self._factory = factory or create_rapidocr
        self._lock = threading.Lock()
        self._engine: Any = None

    def recognize(self, image_path: Path) -> RecognizedPage:
        from PIL import Image

        with self._lock:
            if self._engine is None:
                self._engine = self._factory()
            with Image.open(image_path) as image:
                width, height = image.size
                factor = downscale_factor(width, height)
                if factor < 1.0:
                    size = (max(1, round(width * factor)), max(1, round(height * factor)))
                    log.info(
                        "%s: downscaling %dx%d px to %dx%d px for recognition",
                        image_path.name, width, height, *size,
                    )
                    source: Any = image.convert("RGB").resize(size, Image.Resampling.LANCZOS)
                    scale_x, scale_y = size[0] / width, size[1] / height
                else:
                    source = str(image_path)
                    scale_x = scale_y = 1.0
            result = self._run(source, image_path)
        return RecognizedPage(width, height, lines_from_result(result, scale_x, scale_y))

    def _run(self, source: Any, image_path: Path) -> Any:
        try:
            return self._engine(source, return_word_box=True)
        except IndexError:
            # RapidOCR 3.9.2 indexes word groups per line after dropping the
            # groups of lines without word boxes (filter_by_text_score).
            log.warning(
                "%s: RapidOCR could not align word boxes; spreading words evenly within their lines",
                image_path.name,
            )
            return self._engine(source, return_word_box=False)

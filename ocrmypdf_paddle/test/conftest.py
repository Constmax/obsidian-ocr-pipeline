"""Puts src/ on sys.path so the tests run without installing the plugin.

The package imports ocrmypdf, so these tests run in the CI job that installs
the pinned OCRmyPDF (`ocrmypdf`), not in the plain Python job. RapidOCR, ONNX
Runtime and the model weights are never needed. Without ocrmypdf the tests
are skipped at collection, unless REQUIRE_OCRMYPDF=1 (set in CI).
"""
import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

if importlib.util.find_spec("ocrmypdf") is None and os.environ.get("REQUIRE_OCRMYPDF") != "1":
    collect_ignore_glob = ["test_*.py"]

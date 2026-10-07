"""`reprocess-raw --check-engine` (issue #73).

The plugin asks before it offers an engine: exit 0 when a run with these
options would start on that engine, exit 4 (`check-failed`, contracts/) with
the reason on stderr otherwise. PaddleOCR counts as usable only when its
OCRmyPDF plugin loads *and* `--paddle-check <mode>` reports it ready, so a
run never falls back to another engine just because a model or RapidOCR is
missing. The toolchain is the stubbed one of test_pipeline.py.

Run with: python3 -m pytest bin/test -q
"""
import os
import subprocess
from pathlib import Path

import pytest

from test_pipeline import BASH, BIN, _write, box  # noqa: F401  (fixture)


pytestmark = pytest.mark.slow  # runs the CLI end to end; `make test-fast` skips it


def _check(box, *options, **fake):
    env = dict(box.env, **{f"FAKE_{k.upper()}": v for k, v in fake.items()})
    return subprocess.run(
        [BASH, str(BIN / "reprocess-raw.sh"), "--check-engine", *options],
        env=env, capture_output=True, text=True, timeout=30,
    )


def test_ready_paddle_fast(box):
    result = _check(box, "--engine", "paddle", "--paddle-mode", "fast", paddle="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "🧠 Engine:    PaddleOCR fast (manual)" in result.stdout
    [probe] = [c for c in box.calls("ocrmypdf") if "--paddle-check" in c]
    assert probe.endswith("--plugin ocrmypdf_paddle --paddle-check fast"), probe


def test_unready_paddle_names_the_reason(box):
    result = _check(box, "--engine", "paddle", "--paddle-mode", "fast",
                    paddle="1", paddle_unready="model file missing: /models/x.onnx")

    assert result.returncode == 4, result.stdout + result.stderr
    assert "PaddleOCR engine is not ready:" in result.stderr
    assert "model file missing: /models/x.onnx" in result.stderr


def test_paddle_plugin_missing(box):
    result = _check(box, "--engine", "paddle", "--paddle-mode", "fast")

    assert result.returncode == 4
    assert "PaddleOCR engine plugin not installed" in result.stderr


def test_other_engines_are_checked_too(box):
    assert _check(box, "--engine", "apple").returncode == 4
    assert _check(box, "--engine", "apple", apple="1").returncode == 0
    ok = _check(box)  # auto
    assert ok.returncode == 0 and "Tesseract (auto-fallback)" in ok.stdout


def test_auto_names_a_ready_paddle(box):
    """#198: the check reports the engine a run would use."""
    result = _check(box, apple="1", paddle="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "🧠 Engine:    PaddleOCR fast (auto)" in result.stdout
    [probe] = [c for c in box.calls("ocrmypdf") if "--paddle-check" in c]
    assert probe.endswith("--paddle-check fast"), probe


def test_check_touches_no_file(box):
    _check(box, "--engine", "paddle", paddle="1")

    assert box.files() == {}
    assert [c for c in box.calls("ocrmypdf") if "--paddle-check" not in c] == []


def test_usage_errors_stay_usage_errors(box):
    result = _check(box, "--engine", "easyocr")

    assert result.returncode == 1
    assert "--engine must be 'auto', 'apple', 'tesseract', or 'paddle'" in result.stderr


def test_a_run_with_unready_paddle_fails_before_ocr(box):
    """Before #73 the plugin loaded, the run failed, and Apple silently took over."""
    _write(box.work, {"a.pdf": "a"})
    result = box.run("pdf-combine.sh", "out", "--engine", "paddle",
                     apple="1", paddle="1", paddle_unready="rapidocr==3.9.2 is not installed")

    assert result.returncode == 1, result.stdout
    assert "rapidocr==3.9.2 is not installed" in result.stderr
    assert [c for c in box.calls("ocrmypdf") if "--paddle-check" not in c] == []


def test_a_plugin_from_before_the_check_asks_for_a_reinstall(box):
    result = _check(box, "--engine", "paddle", paddle="1", paddle_old="1")

    assert result.returncode == 4
    assert "too old for this pipeline" in result.stderr
    assert "usage:" not in result.stderr and "output_pdf" not in result.stderr


def _path_without(tmp_path, tool):
    """PATH with every command of the current PATH except `tool`."""
    links = tmp_path / f"path-without-{tool}"
    links.mkdir()
    for folder in os.environ["PATH"].split(os.pathsep):
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for exe in folder.iterdir():
            target = links / exe.name
            if exe.name != tool and not target.exists() and os.access(exe, os.X_OK):
                target.symlink_to(exe)
    return str(links)


def _remove_tesseract(box, tmp_path):
    stubs = tmp_path / "stubs"
    (stubs / "tesseract").unlink()
    box.env["PATH"] = f"{stubs}{os.pathsep}{_path_without(tmp_path, 'tesseract')}"


def test_missing_tesseract_is_not_usable(box, tmp_path):
    """#217: OCRmyPDF 17.8 needs tesseract with every engine plugin."""
    _remove_tesseract(box, tmp_path)

    result = _check(box, apple="1")

    assert result.returncode == 4, result.stdout + result.stderr
    assert "❌ Missing tools: tesseract" in result.stderr


def test_missing_pikepdf_is_not_usable(box, tmp_path):
    """#217: --in-place and --output check the B5 gate with pikepdf."""
    box.env["VENV_ROOT"] = str(tmp_path / "no-venvs")

    result = _check(box, apple="1")

    assert result.returncode == 4, result.stdout + result.stderr
    assert "❌ pikepdf not found" in result.stderr


def test_a_run_without_tesseract_fails_before_ocr(box, tmp_path):
    _remove_tesseract(box, tmp_path)
    _write(box.work, {"a.pdf": "a"})

    result = box.run("pdf-combine.sh", "out", apple="1")

    assert result.returncode == 1, result.stdout
    assert "❌ Missing tools: tesseract" in result.stderr
    assert box.calls("ocrmypdf") == []

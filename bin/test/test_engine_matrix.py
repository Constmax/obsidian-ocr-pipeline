"""Engine selection and fallback matrix for issue #70.

One resolved engine (apple, tesseract or paddle) decides the OCR arguments
and the retry chain in ocr_with_retry:

- Apple Vision fails   → Tesseract → Tesseract with column split
- Tesseract fails      → Tesseract with column split → Apple Vision
- PaddleOCR fails      → Apple Vision, or Tesseract without it; never an
                         implicit column split

Every fallback is printed on stderr with its reason and named in the
summary. The toolchain is the stubbed one of test_pipeline.py; the ocrmypdf
stub answers per engine (`apple(...)`, `ocr(...)`, `paddle(...)`).

Run with: python3 -m pytest bin/test -q
"""
from pathlib import Path

import pytest

from test_pipeline import _write, box  # noqa: F401  (fixture)


pytestmark = pytest.mark.slow  # end-to-end runs; `make test-fast` skips them


def _ocr_calls(box):
    """OCRmyPDF calls that read pages; the PaddleOCR readiness probe reads none."""
    return [call for call in box.calls("ocrmypdf") if "--paddle-check" not in call.split()]


def _engines(box):
    """Engine of every OCR call, `+split` when it read split halves."""
    sequence = []
    for call in _ocr_calls(box):
        words = call.split()
        engine = "tesseract"
        if "ocrmypdf_appleocr" in words:
            engine = "apple"
        elif "ocrmypdf_paddle" in words:
            engine = "paddle"
        if "split" in Path(words[-2]).name:
            engine += "+split"
        sequence.append(engine)
    return sequence


def _combine(box, *options, **fake):
    _write(box.work, {"a.pdf": "a"})
    return box.run("pdf-combine.sh", "out", *options, **fake)


def _engine_line(result):
    [line] = [line for line in result.stdout.splitlines() if line.startswith("🧠 Engine:   ")]
    return line.removeprefix("🧠 Engine:   ")


# ── Selection ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("engine,apple,expected,label", [
    ("auto", "1", "apple", "Apple Vision (auto)"),
    ("auto", "", "tesseract", "Tesseract (auto-fallback)"),
    ("apple", "1", "apple", "Apple Vision (manual)"),
    ("tesseract", "1", "tesseract", "Tesseract (manual)"),
])
def test_engine_selection(box, engine, apple, expected, label):
    result = _combine(box, "--engine", engine, apple=apple)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _engines(box) == [expected]
    assert _engine_line(result) == label


def test_paddle_arguments(box):
    result = _combine(box, "--engine", "paddle", "--jobs", "4", paddle="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files()["out.pdf"] == "paddle(a)"
    [call] = _ocr_calls(box)
    assert "--plugin ocrmypdf_paddle --paddle-mode accurate " in call, call
    # One OCR job, whatever --jobs says; no Tesseract tuning.
    assert "--jobs 1 " in call and "--jobs 4" not in call, call
    assert "--tesseract-pagesegmode" not in call, call
    assert _engine_line(result) == "PaddleOCR accurate (manual)"


def test_paddle_fast_mode(box):
    result = _combine(box, "--engine", "paddle", "--paddle-mode", "fast", paddle="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "--paddle-mode fast " in _ocr_calls(box)[0]
    assert _engine_line(result) == "PaddleOCR fast (manual)"


@pytest.mark.parametrize("engine,message", [
    ("apple", "Apple Vision plugin not installed"),
    ("paddle", "PaddleOCR engine plugin not installed"),
])
def test_missing_engine_fails_before_ocr(box, engine, message):
    result = _combine(box, "--engine", engine)

    assert result.returncode == 1, result.stdout
    assert message in result.stderr
    assert box.calls("ocrmypdf") == []


@pytest.mark.parametrize("options,message", [
    (["--paddle-mode", "fast"], "--paddle-mode needs --engine paddle"),
    (["--engine", "paddle", "--paddle-mode", "quick"], "--paddle-mode must be 'accurate' or 'fast'"),
    (["--engine", "paddle", "--paddle-mode"], "--paddle-mode needs a value"),
])
@pytest.mark.parametrize("script,positional", [
    ("pdf-auto.sh", []), ("pdf-combine.sh", ["out"]), ("pdf-workflow.sh", ["out"]),
])
def test_paddle_mode_usage_errors(box, script, positional, options, message):
    _write(box.work, {"a.pdf": "a"})

    result = box.run(script, *positional, *options, paddle="1")

    assert result.returncode == 1, result.stdout
    assert message in result.stderr
    assert "Checking dependencies" not in result.stdout
    assert box.calls("ocrmypdf") == []


# ── Fallback matrix ────────────────────────────────────────────────────────

@pytest.mark.parametrize("engine,apple,bad,sequence,output", [
    # Apple Vision fails → Tesseract, then Tesseract with column split.
    ("apple", "1", "apple(a)", ["apple", "tesseract"], "ocr(a)"),
    ("apple", "1", "apple(a) ocr(a)",
     ["apple", "tesseract", "tesseract+split"], "merged(ocr(split(a)))"),
    # Tesseract fails → column split first, then Apple Vision.
    ("tesseract", "1", "ocr(a)", ["tesseract", "tesseract+split"], "merged(ocr(split(a)))"),
    ("tesseract", "1", "ocr(a) ocr(split(a))",
     ["tesseract", "tesseract+split", "apple"], "apple(a)"),
    # PaddleOCR fails → Apple Vision if installed, otherwise Tesseract.
    ("paddle", "1", "paddle(a)", ["paddle", "apple"], "apple(a)"),
    ("paddle", "", "paddle(a)", ["paddle", "tesseract"], "ocr(a)"),
], ids=["apple>tesseract", "apple>tesseract>split", "tesseract>split",
        "tesseract>split>apple", "paddle>apple", "paddle>tesseract"])
def test_fallback_chain(box, engine, apple, bad, sequence, output):
    result = _combine(box, "--engine", engine, apple=apple, paddle="1", bad_text=bad)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _engines(box) == sequence
    assert box.files()["out.pdf"] == output


def test_paddle_never_adds_a_split_retry(box):
    result = _combine(box, "--engine", "paddle", paddle="1", bad_text="paddle(a) ocr(a)")

    assert result.returncode == 1, result.stdout
    assert _engines(box) == ["paddle", "tesseract"]
    assert box.calls("column_tools split") == []
    assert box.files() == {"a.pdf": "a"}


def test_paddle_keeps_an_explicit_split(box):
    result = _combine(box, "--engine", "paddle", "--split-columns", paddle="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _engines(box) == ["paddle+split"]
    assert box.files()["out.pdf"] == "merged(paddle(split(a)))"


def test_tesseract_without_apple_ends_after_the_split_retry(box):
    result = _combine(box, "--engine", "tesseract", bad_text="ocr(a) ocr(split(a))")

    assert result.returncode == 1, result.stdout
    assert _engines(box) == ["tesseract", "tesseract+split"]
    assert "No fallback engine: Apple Vision is not installed" in result.stderr


# ── Visibility: stderr and summary ─────────────────────────────────────────

@pytest.mark.parametrize("fake,reason", [
    ({"bad_text": "paddle(a)"}, "quality gate failed"),
    ({"ocr_fail": "paddle"}, "OCR failed"),
])
def test_fallback_is_reported_with_its_reason(box, fake, reason):
    result = _combine(box, "--engine", "paddle", apple="1", paddle="1", **fake)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"🔄 Fallback: PaddleOCR accurate → Apple Vision ({reason})" in result.stderr
    assert _engine_line(result) == f"Apple Vision (fallback from PaddleOCR accurate: {reason})"


def test_split_retry_is_named_in_the_summary(box):
    result = _combine(box, "--engine", "tesseract", bad_text="ocr(a)")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _engine_line(result) == "Tesseract (manual), column split retry"


def test_auto_reports_fallbacks_per_group_and_in_total(box):
    _write(box.work, {"a.pdf": "a", "b.pdf": "b"})

    result = box.run("pdf-auto.sh", apple="1", bad_text="apple(a)")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files(box.work / "_processed") == {"a.pdf": "ocr(a)", "b.pdf": "apple(b)"}
    assert "   🔄 Engine: Tesseract (fallback from Apple Vision: quality gate failed)" in result.stdout
    assert "🔄 Fallbacks:  1" in result.stdout

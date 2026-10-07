"""Issue #218: slide decks and sparse pages under the quality gate.

The document-wide gate fails a result under 200 characters per page on
average and then walks the whole fallback chain. reprocess-raw checks every
page itself (B5, at least 50 characters, with exemptions), so on its path
the gate's average drops to 1 character per page: a slide deck gets its text
layer from one OCR pass, and only a pass that read no text at all moves to
the next engine. The garbage and split checks stay. pdf-combine on its own
keeps the average of 200.

The toolchain is the stubbed one of test_pipeline.py.
"""
import subprocess
from pathlib import Path

import pytest

from test_pipeline import BASH, box, failure_reason  # noqa: F401  (fixture)


pytestmark = pytest.mark.slow  # end-to-end runs; `make test-fast` skips them

BIN = Path(__file__).resolve().parent.parent
SLIDE_CHARS = "120"  # per page: over the B5 floor, under the average of 200


def _ocr_calls(box):
    return [call for call in box.calls("ocrmypdf") if "--paddle-check" not in call.split()]


def _reprocess(box, tmp_path, *args, **fake):
    deck = tmp_path / "vault" / "deck.pdf"
    deck.parent.mkdir()
    deck.write_text("deck")
    env = dict(box.env, **{f"FAKE_{k.upper()}": v for k, v in fake.items()})
    result = subprocess.run(
        [BASH, str(BIN / "reprocess-raw.sh"), str(deck), *args],
        env=env, capture_output=True, text=True, timeout=30, check=False)
    return deck, result


@pytest.mark.parametrize("apple", ["", "1"])
def test_in_place_gives_a_slide_deck_its_text_layer_in_one_ocr_pass(box, tmp_path, apple):
    deck, result = _reprocess(box, tmp_path, "--in-place",
                              text_chars=SLIDE_CHARS, b5_pass="1", apple=apple)

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_ocr_calls(box)) == 1, _ocr_calls(box)
    assert deck.read_text() in {"ocr(deck)", "apple(deck)"}


def test_output_gives_a_slide_deck_a_searchable_copy_in_one_ocr_pass(box, tmp_path):
    out = tmp_path / "vault" / "deck-ocr.pdf"
    deck, result = _reprocess(box, tmp_path, "--output", str(out),
                              text_chars=SLIDE_CHARS, b5_pass="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_ocr_calls(box)) == 1, _ocr_calls(box)
    assert out.read_text() == "ocr(deck)"
    assert deck.read_text() == "deck"


def test_reprocess_raw_moves_on_from_a_pass_without_any_text(box, tmp_path):
    deck, result = _reprocess(box, tmp_path, "--in-place", apple="1",
                              bad_text="apple(deck)", b5_pass="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_ocr_calls(box)) == 2, _ocr_calls(box)
    assert deck.read_text() == "ocr(deck)"


def test_reprocess_raw_still_rejects_pages_under_the_b5_floor(box, tmp_path):
    deck, result = _reprocess(box, tmp_path, "--in-place", text_chars=SLIDE_CHARS)

    assert result.returncode == 1, result.stdout + result.stderr
    assert len(_ocr_calls(box)) == 1, _ocr_calls(box)
    assert "B5 gate failed" in result.stdout
    assert deck.read_text() == "deck"


def test_pdf_combine_alone_keeps_the_average_check(box):
    (box.work / "a.pdf").write_text("a")
    result = box.run("pdf-combine.sh", "out", text_chars=SLIDE_CHARS)

    assert result.returncode != 0, result.stdout + result.stderr
    assert failure_reason(result) == (
        "❌ Quality gate failed: only 120 characters per page (min: 200) — no file written")

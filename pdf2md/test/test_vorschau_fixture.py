#!/usr/bin/env python3
"""Pytest: recomputes fixture via pure functions and compares with committed state.
"""

from pathlib import Path

from generate_preview_fixture import fixture_text
from assembly import build_frontmatter


def test_frontmatter_aborted_only_when_set():
    """Issue #25: aborted field appears only when cancellation occurs."""
    without = build_frontmatter(
        title="t", source_pdf_path=Path("a.pdf"), pages=10,
        pages_textlayer=5, pages_ocr=5,
        ocr_date="2026-08-15", ocr_timestamp="2026-08-15T10:00:00")
    assert "abgebrochen:" not in without

    with_aborted = build_frontmatter(
        title="t", source_pdf_path=Path("a.pdf"), pages=10,
        pages_textlayer=5, pages_ocr=5,
        ocr_date="2026-08-15", ocr_timestamp="2026-08-15T10:00:00",
        aborted="seite 5 von 10")
    assert "abgebrochen: seite 5 von 10" in with_aborted


def test_preview_fixture_unchanged():
    recomputed = fixture_text()

    fixture_path = (
        Path(__file__).resolve().parent.parent.parent
        / "plugin" / "test" / "fixtures" / "beispiel-vorschau.md"
    )
    committed = fixture_path.read_text(encoding="utf-8")

    assert recomputed == committed, (
        "Fixture differs from committed state — "
        f"Generated {len(recomputed)} chars, committed {len(committed)} chars."
    )

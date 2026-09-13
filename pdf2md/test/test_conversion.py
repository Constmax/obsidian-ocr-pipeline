"""Tests for the document conversion interface (Issue #51)."""

from pathlib import Path

import fitz

from assembly import AssemblyContext, assemble_paragraphs
from conversion import ConversionRequest, convert_document


def _make_vector_pdf(path: Path, pages=2):
    with fitz.open() as doc:
        for number in range(1, pages + 1):
            page = doc.new_page(width=600, height=800)
            page.insert_text(
                fitz.Point(20, 100),
                f"Page {number}: " + "x" * 180,
                fontsize=5,
            )
        doc.save(path)


def test_convert_document_without_argparse_or_ocr_model(tmp_path):
    pdf = tmp_path / "input.pdf"
    output = tmp_path / "output"
    _make_vector_pdf(pdf)
    events = []

    def unexpected_ocr(*_args, **_kwargs):
        raise AssertionError("vector pages must not invoke the OCR adapter")

    result = convert_document(
        ConversionRequest(pdf=pdf, output_dir=output),
        unexpected_ocr,
        events.append,
    )

    assert result.completed is True
    assert result.cancelled is False
    assert result.pages_textlayer == 2
    assert result.pages_ocr == 0
    assert result.target == output / "input.md"
    assert result.target.read_text(encoding="utf-8") == result.markdown
    assert [event["type"] for event in events] == [
        "analysis_started", "analysis_complete", "start", "page", "page",
        "complete",
    ]


def test_cancelled_conversion_uses_the_normal_result_writer(tmp_path):
    pdf = tmp_path / "partial.pdf"
    output = tmp_path / "output"
    _make_vector_pdf(pdf, pages=3)
    cancelled = False

    def event_sink(event):
        nonlocal cancelled
        if event["type"] == "page":
            cancelled = True

    result = convert_document(
        ConversionRequest(
            pdf=pdf,
            output_dir=output,
            cancel_requested=lambda: cancelled,
        ),
        None,
        event_sink,
    )

    assert result.completed is False
    assert result.cancelled is True
    assert len(result.pages) == 1
    assert result.target == output / "partial.md"
    assert "abgebrochen: seite 1 von 3" in result.markdown
    assert result.target.read_text(encoding="utf-8") == result.markdown


def test_running_headers_are_scoped_to_the_assembly_request():
    lines = [["Repeated document heading", (0, 20, 500, 40)]]
    with_header = assemble_paragraphs(
        lines, AssemblyContext(frozenset({"Repeated document heading"})))
    without_header = assemble_paragraphs(lines, AssemblyContext())

    assert with_header.paragraphs == []
    assert with_header.discarded == ["Repeated document heading"]
    assert without_header.paragraphs == ["Repeated document heading"]
    assert without_header.discarded == []

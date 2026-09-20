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


def test_scan_page_images_stay_out_of_the_output_folder(tmp_path):
    """--out is a vault folder in plugin runs; intermediates must not land there."""
    pdf = tmp_path / "scan.pdf"
    output = tmp_path / "output"
    temp_root = tmp_path / "scratch"
    _make_vector_pdf(pdf, pages=1)
    seen_temp_parents = []

    def fake_ocr(image, max_tokens=None):
        seen_temp_parents.append(Path(image).parent.parent)
        return ""

    convert_document(
        ConversionRequest(pdf=pdf, output_dir=output, ocr_only=True,
                          temp_root=temp_root, no_dictionary=True),
        fake_ocr,
    )

    assert seen_temp_parents == [temp_root]
    # .cache/ is the Issue #11 page cache — intentionally under --out, unlike
    # the scan-page renders above, so a stopped run can resume from it.
    assert [entry.name for entry in sorted(output.iterdir())] == [
        ".cache", "scan.md"]


def test_the_adapter_is_prepared_before_pages_are_timed(tmp_path):
    """The one-off model load must not be charged to the first OCR page."""
    pdf = tmp_path / "scan.pdf"
    _make_vector_pdf(pdf, pages=1)
    calls = []

    class Adapter:
        def prepare(self):
            calls.append("prepare")

        def __call__(self, image, max_tokens=None):
            calls.append("ocr")
            return ""

    convert_document(
        ConversionRequest(pdf=pdf, output_dir=tmp_path / "output",
                          ocr_only=True, temp_root=tmp_path / "scratch",
                          no_dictionary=True),
        Adapter(),
    )

    assert calls[0] == "prepare"
    assert calls.count("prepare") == 1
    assert "ocr" in calls


def test_written_side_files_are_announced(tmp_path):
    pdf = tmp_path / "input.pdf"
    dump = tmp_path / "lines.json"
    _make_vector_pdf(pdf, pages=2)
    events = []

    convert_document(
        ConversionRequest(pdf=pdf, output_dir=tmp_path / "output",
                          lines_dump=dump),
        None,
        events.append,
    )

    artifacts = [event for event in events if event["type"] == "artifact"]
    assert [(event["kind"], event["count"], event["unit"])
            for event in artifacts] == [("lines_dump", 2, "pages")]
    assert artifacts[0]["path"] == dump


def test_image_only_diagram_pages_stay_out_of_the_lines_dump(tmp_path):
    import json

    pdf = tmp_path / "input.pdf"
    dump = tmp_path / "lines.json"
    _make_vector_pdf(pdf, pages=2)

    convert_document(
        ConversionRequest(pdf=pdf, output_dir=tmp_path / "output",
                          lines_dump=dump, ocr_only=True,
                          temp_root=tmp_path / "scratch", no_dictionary=True,
                          forced_diagram_pages=frozenset({1}),
                          diagram_image_only=True),
        lambda image, max_tokens=None: "",
    )

    assert [entry["seite"] for entry in json.loads(dump.read_text())] == [2]

"""Tests for the document conversion interface (Issue #51)."""

from pathlib import Path

import fitz
import pytest

from assembly import (AssemblyContext, PreviewFormatError, assemble_paragraphs,
                      split_preview)
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


# --- A --pages run merges into the existing preview (Issue #106) ------------

def _frontmatter(markdown):
    return split_preview(markdown).fields


def _blocks(markdown):
    return {page.number: page.text for page in split_preview(markdown).pages}


def test_a_pages_run_replaces_only_its_pages_in_the_existing_preview(tmp_path):
    pdf = tmp_path / "skript.pdf"
    output = tmp_path / "output"
    _make_vector_pdf(pdf, pages=10)
    target = convert_document(
        ConversionRequest(pdf=pdf, output_dir=output), None).target
    edited = target.read_text(encoding="utf-8").replace(
        "Page 5: ", "Page 5 (checked by hand): ")
    target.write_text(edited, encoding="utf-8")
    before = _blocks(edited)

    result = convert_document(
        ConversionRequest(pdf=pdf, output_dir=output, ocr_only=True,
                          temp_root=tmp_path / "scratch", no_dictionary=True,
                          selected_pages=frozenset({3})),
        lambda image, max_tokens=None: "Neu erkannte Seite drei",
    )

    merged = target.read_text(encoding="utf-8")
    after = _blocks(merged)
    assert sorted(after) == list(range(1, 11))
    assert {number for number in after if after[number] != before[number]} == {3}
    assert after[3].startswith("%% S. 3 | ocr")
    assert "Neu erkannte Seite drei" in after[3]
    assert "Page 5 (checked by hand): " in after[5]
    fields = _frontmatter(merged)
    assert (fields["seiten"], fields["seiten-textlayer"],
            fields["seiten-ocr"]) == ("10", "9", "1")
    assert "abgebrochen" not in fields
    assert split_preview(merged).preamble == split_preview(edited).preamble
    assert result.markdown == merged
    assert [page.number for page in result.pages] == [3]


def test_a_preview_without_page_markers_is_left_alone(tmp_path):
    pdf = tmp_path / "notes.pdf"
    output = tmp_path / "output"
    output.mkdir()
    _make_vector_pdf(pdf, pages=3)
    target = output / "notes.md"
    target.write_text("---\ntitel: notes\n---\nMy own notes\n", encoding="utf-8")

    def unexpected_ocr(*_args, **_kwargs):
        raise AssertionError("no page may be converted")

    with pytest.raises(PreviewFormatError, match="no page markers"):
        convert_document(
            ConversionRequest(pdf=pdf, output_dir=output, ocr_only=True,
                              selected_pages=frozenset({2})),
            unexpected_ocr,
        )

    assert target.read_text(encoding="utf-8") == "---\ntitel: notes\n---\nMy own notes\n"


def test_filling_the_gap_of_a_cancelled_run_drops_its_abort_note(tmp_path):
    pdf = tmp_path / "partial.pdf"
    output = tmp_path / "output"
    _make_vector_pdf(pdf, pages=4)
    pages_done = 0

    def stop_after_two(event):
        nonlocal pages_done
        pages_done += event["type"] == "page"

    convert_document(
        ConversionRequest(pdf=pdf, output_dir=output,
                          cancel_requested=lambda: pages_done >= 2),
        None, stop_after_two)
    assert _frontmatter((output / "partial.md").read_text())["abgebrochen"] \
        == "seite 2 von 4"

    one_more = convert_document(ConversionRequest(
        pdf=pdf, output_dir=output, selected_pages=frozenset({3})), None).markdown
    assert _frontmatter(one_more)["abgebrochen"] == "seite 2 von 4"
    assert _frontmatter(one_more)["seiten"] == "3"

    filled = convert_document(ConversionRequest(
        pdf=pdf, output_dir=output, selected_pages=frozenset({4})), None).markdown
    assert "abgebrochen" not in _frontmatter(filled)
    assert sorted(_blocks(filled)) == [1, 2, 3, 4]


def test_a_cancelled_pages_run_keeps_the_pages_it_did_not_reach(tmp_path):
    pdf = tmp_path / "input.pdf"
    output = tmp_path / "output"
    _make_vector_pdf(pdf, pages=4)
    convert_document(ConversionRequest(pdf=pdf, output_dir=output), None)
    before = _blocks((output / "input.md").read_text())
    cancelled = False

    def cancel_after_first(event):
        nonlocal cancelled
        cancelled = cancelled or event["type"] == "page"

    result = convert_document(
        ConversionRequest(pdf=pdf, output_dir=output, ocr_only=True,
                          temp_root=tmp_path / "scratch", no_dictionary=True,
                          selected_pages=frozenset({2, 3}),
                          cancel_requested=lambda: cancelled),
        lambda image, max_tokens=None: "Neu", cancel_after_first)

    after = _blocks(result.markdown)
    assert result.cancelled is True
    assert sorted(after) == [1, 2, 3, 4]
    assert after[2] != before[2]
    assert after[3] == before[3]
    assert "abgebrochen" not in _frontmatter(result.markdown)


def test_kept_pages_bring_their_derailment_from_the_page_cache(tmp_path):
    import json

    pdf = tmp_path / "input.pdf"
    output = tmp_path / "output"
    _make_vector_pdf(pdf, pages=3)
    convert_document(ConversionRequest(pdf=pdf, output_dir=output), None)
    entry_path = output / ".cache" / "input" / "002.json"
    entry = json.loads(entry_path.read_text())
    entry["page"]["trace"] = ["repetition loop, retried as tiles"]
    entry_path.write_text(json.dumps(entry))

    merged = convert_document(ConversionRequest(
        pdf=pdf, output_dir=output, selected_pages=frozenset({1})), None).markdown

    assert _frontmatter(merged)["seiten-entgleist"] == "1"

"""Spike #95 harness tests: pure, no Docling, no model, no vault."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import docling_adapter as adapter  # noqa: E402
import spike_docling as spike  # noqa: E402


# ── Adapter stays evaluation-only and dependency-free at import ──────────


def test_adapter_has_no_top_level_docling_import():
    source = (Path(__file__).resolve().parent
              / "docling_adapter.py").read_text(encoding="utf-8")
    top_level_docling = [
        line for line in source.splitlines()
        if line.startswith(("import docling", "from docling"))]
    assert top_level_docling == []


def test_missing_dependencies_reports_a_reason_or_nothing():
    problems = adapter.missing_dependencies()
    assert isinstance(problems, list)
    assert adapter.docling_available() == (problems == [])


def test_converter_kwargs_is_empty_by_design():
    assert adapter.converter_kwargs() == {}


def test_docling_version_is_none_or_a_string():
    assert (adapter.docling_version() is None
            or isinstance(adapter.docling_version(), str))


def test_convert_missing_pdf_raises_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        adapter.convert_pdf_to_markdown(tmp_path / "nope.pdf")


def test_convert_without_docling_explains_install(tmp_path):
    if adapter.docling_available():
        pytest.skip("docling is installed here")
    pdf = tmp_path / "page.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    with pytest.raises(RuntimeError, match="pip install docling"):
        adapter.convert_pdf_to_markdown(pdf)


def test_page_records_to_texts_and_jsonable():
    records = [adapter.DoclingPageRecord("t01", "t01.pdf", "# hi\n", 4, 1.5)]
    assert adapter.page_records_to_texts(records) == {"t01": "# hi\n"}
    jsonable = adapter.records_to_jsonable(records)
    assert jsonable == [{"page_id": "t01", "source_pdf": "t01.pdf",
                         "markdown": "# hi\n", "chars": 4, "seconds": 1.5}]
    json.dumps(jsonable)  # must serialise for result.json


# ── pdf2md page extraction and timing summaries ──────────────────────────


def test_extract_pdf2md_page_markdown_drops_frontmatter():
    document = ("---\ntitle: x\n---\nQuelle: [[a.pdf]]\n"
                "%% S. 1 | ocr %%\n# Title\n\nBody.\n")
    assert spike.extract_pdf2md_page_markdown(document) == "# Title\n\nBody.\n"


def test_extract_pdf2md_page_markdown_without_marker_is_identity():
    assert spike.extract_pdf2md_page_markdown("plain") == "plain"


def test_summarize_timings_empty_and_filled():
    assert spike.summarize_timings([])["pages"] == 0
    summary = spike.summarize_timings(
        [{"seconds": 2.0}, {"seconds": 4.0}, {"seconds": 6.0}])
    assert (summary["pages"], summary["median_s"], summary["mean_s"],
            summary["min_s"], summary["total_s"]) == (3, 4.0, 4.0, 2.0, 12.0)


def test_peak_rss_is_positive_mib():
    assert spike.peak_rss_mb() > 0
    assert spike._rss_divisor() in (1024, 2 ** 20)


# ── Scoring both paths with the shared reading-order metric ─────────────


def _two_column_fixture():
    regions = [{"role": "body", "column": "L", "box": [0, 0, 500, 1000]},
               {"role": "body", "column": "R", "box": [500, 0, 1000, 1000]}]

    def line(text, x0, y0, x1, y1):
        return {"text": text,
                "polygon": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}

    lines = [line("erste Zeile links oben mit Inhalt", 50, 100, 450, 140),
             line("zweite Zeile links unten mit Inhalt", 50, 200, 450, 240),
             line("dritte Zeile rechts oben mit Inhalt", 550, 100, 950, 140),
             line("vierte Zeile rechts unten mit Inhalt", 550, 200, 950, 240)]
    specs = [{"id": "t99", "regions": regions}]
    records = {"t99": {"width": 1000, "height": 1000, "lines": lines}}
    left = ["erste Zeile links oben mit Inhalt",
            "zweite Zeile links unten mit Inhalt"]
    right = ["dritte Zeile rechts oben mit Inhalt",
             "vierte Zeile rechts unten mit Inhalt"]
    return specs, records, left, right


def test_score_texts_prefers_correct_column_order():
    specs, records, left, right = _two_column_fixture()
    docling = {"t99": "\n".join(left + right)}
    current = {"t99": "\n".join([left[0], right[0], left[1], right[1]])}
    per_page, summaries = spike._score_texts(docling, current, specs, records)
    assert per_page["docling"]["t99"]["accuracy"] == 1.0
    assert per_page["pdf2md"]["t99"]["accuracy"] < 1.0
    assert summaries["docling"]["median"] == 1.0


def test_decision_guidance_branches():
    good = {"docling": {"median": 0.97}, "pdf2md": {"median": 0.90}}
    fast = {"docling": {"median_s": 5.0}, "pdf2md": {"median_s": 30.0}}
    assert spike.decision_guidance(good, fast)[0] == "migration candidate"
    bad = {"docling": {"median": 0.80}, "pdf2md": {"median": 0.90}}
    assert spike.decision_guidance(bad, fast)[0] == "keep current"
    tied = {"docling": {"median": 0.90}, "pdf2md": {"median": 0.90}}
    assert spike.decision_guidance(tied, fast)[0] == "inconclusive"
    assert spike.decision_guidance({}, {})[0] == "inconclusive"


def test_format_comparison_table_lists_pages_and_engines():
    per_page = {"docling": {"t99": {"accuracy": 1.0}},
                "pdf2md": {"t99": {"accuracy": 0.5}}}
    summaries = {"docling": {"median": 1.0}, "pdf2md": {"median": 0.5}}
    timings = {"docling": {"pages": 1, "median_s": 5.0, "peak_rss_mb": 800},
               "pdf2md": {"pages": 1, "median_s": 30.0, "peak_rss_mb": 1100}}
    table = spike.format_comparison_table(per_page, summaries, timings)
    assert "| t99 |" in table and "| docling |" in table


# ── CLI stays runnable without models or vault ───────────────────────────


def test_check_reports_status_without_models():
    bench = Path(__file__).resolve().parent
    result = subprocess.run(
        [sys.executable, str(bench / "spike_docling.py"), "check"],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "docling" in result.stdout.lower()


def test_run_and_compare_help_without_models():
    bench = Path(__file__).resolve().parent
    for argv in (["run", "--help"], ["compare", "--help"]):
        result = subprocess.run(
            [sys.executable, str(bench / "spike_docling.py"), *argv],
            capture_output=True, text=True, check=False)
        assert result.returncode == 0

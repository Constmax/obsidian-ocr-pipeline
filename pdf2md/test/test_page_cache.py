"""Page-cache unit and CLI integration tests (Issue #11)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import fitz

import page_cache
import pdf2md as pdf2md_cli


REPO = Path(__file__).resolve().parent.parent.parent
PDF2MD = REPO / "pdf2md" / "pdf2md.py"


def _make_vector_pdf(path: Path, pages: int = 3, suffix: str = "") -> None:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=600, height=800)
        page.insert_text(
            fitz.Point(20, 100),
            f"Page {i + 1} {suffix} " + "x" * 180,
            fontsize=5,
        )
    doc.save(path)
    doc.close()


def _run(pdf: Path, out: Path, *extra: str,
         env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PDF2MD), str(pdf), "--out", str(out), *extra],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _without_timestamp(markdown: str) -> str:
    return "\n".join(
        line for line in markdown.splitlines()
        if not line.startswith("ocr-zeitpunkt:")
    )


def test_roundtrip_and_mismatched_key_is_a_miss(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"pdf contents")
    context = page_cache.build_context(pdf, {"dpi": 150, "model": "test@1"})
    directory = page_cache.cache_directory(tmp_path / "out", pdf)
    page = {
        "number": 1,
        "source": "ocr",
        "characters": 0,
        "layout": "single-column",
        "mode": "whole",
        "lines": [["Text", [10, 20, 900, 40]]],
        "trace": ["retry at depth 1"],
    }

    target = page_cache.write_page(directory, context, page)

    assert target.name == "001.json"
    assert page_cache.read_page(
        directory, 1, page_cache.page_key(context, 1)) == page
    changed = page_cache.build_context(pdf, {"dpi": 300, "model": "test@1"})
    assert page_cache.read_page(
        directory, 1, page_cache.page_key(changed, 1)) is None


def test_corrupt_entry_is_a_miss(tmp_path):
    directory = tmp_path / ".cache" / "source"
    directory.mkdir(parents=True)
    page_cache.page_path(directory, 1).write_text("not json", encoding="utf-8")

    assert page_cache.read_page(directory, 1, "unused") is None


def test_incomplete_entry_is_a_miss(tmp_path):
    directory = tmp_path / ".cache" / "source"
    directory.mkdir(parents=True)
    page_cache.page_path(directory, 1).write_text(json.dumps({
        "schema": page_cache.CACHE_SCHEMA,
        "key": "expected",
        "page": {"number": 1, "source": "ocr", "lines": []},
    }), encoding="utf-8")

    assert page_cache.read_page(directory, 1, "expected") is None


def test_cli_reuses_cache_and_reassembles_identical_markdown(tmp_path):
    pdf = tmp_path / "source.pdf"
    out = tmp_path / "out"
    out.mkdir()
    _make_vector_pdf(pdf)

    first = _run(pdf, out)
    assert first.returncode == 0, first.stderr
    original = (out / "source.md").read_text(encoding="utf-8")
    cache_files = sorted((out / ".cache" / "source").glob("*.json"))
    assert [path.name for path in cache_files] == ["001.json", "002.json", "003.json"]

    second = _run(pdf, out)
    assert second.returncode == 0, second.stderr
    resumed = (out / "source.md").read_text(encoding="utf-8")

    assert "cache: 3 page(s) reused" in second.stdout
    assert second.stdout.count("(cache)") == 3
    assert _without_timestamp(resumed) == _without_timestamp(original)


def test_cached_ocr_page_does_not_import_model(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    out = tmp_path / "out"
    out.mkdir()
    doc = fitz.open()
    doc.new_page(width=600, height=800)
    doc.save(pdf)
    doc.close()

    monkeypatch.setenv("MLX_OCR_MODEL_REVISION", "fixture-revision")
    context = page_cache.build_context(pdf, {
        "dpi": 150,
        "tile_from": pdf2md_cli.TILE_THRESHOLD,
        "bold": True,
        "ocr_only": False,
        "retries": 1,
        "model": pdf2md_cli.MODEL,
        "model_revision": "fixture-revision",
        "prompt": pdf2md_cli.PROMPT,
        "diagram_image_only": False,
        "diagram_pages": [],
    })
    page_cache.write_page(page_cache.cache_directory(out, pdf), context, {
        "number": 1,
        "source": "ocr",
        "characters": 0,
        "layout": "einspaltig",
        "mode": "ganz",
        "lines": [["Cached OCR text", [100, 100, 900, 120]]],
        "trace": [],
    })
    blocker = tmp_path / "blocked-import"
    blocker.mkdir()
    (blocker / "mlx_vlm.py").write_text(
        "raise RuntimeError('mlx_vlm must not be imported')\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(blocker)

    result = _run(pdf, out, env=env)

    assert result.returncode == 0, result.stderr
    assert "cache: 1 page(s) reused" in result.stdout
    assert "Cached OCR text" in (out / "scan.md").read_text(encoding="utf-8")


def test_cli_refreshes_only_requested_page(tmp_path):
    pdf = tmp_path / "source.pdf"
    out = tmp_path / "out"
    out.mkdir()
    _make_vector_pdf(pdf)
    assert _run(pdf, out).returncode == 0

    result = _run(pdf, out, "--neu", "2")

    assert result.returncode == 0, result.stderr
    assert "cache: 2 page(s) reused" in result.stdout
    assert result.stdout.count("(cache)") == 2
    assert "p.2:" in result.stdout


def test_parameter_and_pdf_content_changes_invalidate_cache(tmp_path):
    pdf = tmp_path / "source.pdf"
    out = tmp_path / "out"
    out.mkdir()
    _make_vector_pdf(pdf)
    assert _run(pdf, out).returncode == 0

    dpi_changed = _run(pdf, out, "--dpi", "200")
    assert dpi_changed.returncode == 0, dpi_changed.stderr
    assert "page(s) reused" not in dpi_changed.stdout

    replacement = tmp_path / "replacement.pdf"
    _make_vector_pdf(replacement, suffix="changed")
    replacement.replace(pdf)
    pdf_changed = _run(pdf, out, "--dpi", "200")
    assert pdf_changed.returncode == 0, pdf_changed.stderr
    assert "page(s) reused" not in pdf_changed.stdout


def test_refresh_without_range_recalculates_all_selected_pages(tmp_path):
    pdf = tmp_path / "source.pdf"
    out = tmp_path / "out"
    out.mkdir()
    _make_vector_pdf(pdf)
    assert _run(pdf, out).returncode == 0

    result = _run(pdf, out, "--seiten", "1,3", "--neu")

    assert result.returncode == 0, result.stderr
    assert "page(s) reused" not in result.stdout
    assert "%% S. 2 " not in (out / "source.md").read_text(encoding="utf-8")


def test_checked_in_page_cache_fixture_is_assemblable_without_mlx():
    fixture = Path(__file__).parent / "data" / "page-cache"
    paths = sorted(fixture.glob("*.json"))
    assert [path.name for path in paths] == ["001.json", "002.json"]

    from assembly import assemble_paragraphs

    paragraphs = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        number = payload["page"]["number"]
        expected_key = page_cache.page_key(payload["context"], number)
        assert payload["key"] == expected_key, (
            f"{path.name}: stored key must be the real page_key, "
            "not a placeholder, so the fixture exercises cache validation"
        )
        page = page_cache.read_page(fixture, number, expected_key)
        assert page is not None, f"{path.name}: fixture must pass read_page"
        result = assemble_paragraphs(page["lines"])
        assert result.paragraphs
        paragraphs.append(result.paragraphs)
    assert "§ 985 BGB" in " ".join(paragraphs[0])


def test_unresolved_model_revision_never_reuses(tmp_path, monkeypatch):
    monkeypatch.delenv("MLX_OCR_MODEL_REVISION", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "empty-hub"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "empty-home"))

    first = page_cache.model_revision("mlx-community/PaddleOCR-VL-1.5-4bit")
    second = page_cache.model_revision("mlx-community/PaddleOCR-VL-1.5-4bit")

    assert first.startswith("unresolved-")
    assert second.startswith("unresolved-")
    assert first != second, (
        "unknown revisions must not share a key across runs "
        "(Issue #11: silent reuse is worse than loss)"
    )


def test_corrupt_box_is_a_miss(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"pdf contents")
    context = page_cache.build_context(pdf, {"dpi": 150, "model": "test@1"})
    directory = page_cache.cache_directory(tmp_path / "out", pdf)
    page_cache.write_page(directory, context, {
        "number": 1,
        "source": "ocr",
        "characters": 0,
        "layout": "single-column",
        "mode": "whole",
        "lines": [["Text", [10, 20, 900, 40]]],
        "trace": [],
    })
    expected = page_cache.page_key(context, 1)
    assert page_cache.read_page(directory, 1, expected) is not None

    payload = json.loads(page_cache.page_path(directory, 1).read_text(
        encoding="utf-8"))
    payload["page"]["lines"] = [["Text", "not-a-box"]]
    page_cache.page_path(directory, 1).write_text(
        json.dumps(payload), encoding="utf-8")

    assert page_cache.read_page(directory, 1, expected) is None


def test_textlayer_cache_survives_model_upgrade(tmp_path):
    from conversion import ConversionRequest, _cache_contexts, _page_cache_key
    from conversion import AnalyzedPage

    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"pdf contents")
    page = AnalyzedPage(
        number=1, image_path=None, text_characters=200,
        layout_type="vektoriell", gutter=None,
        textlayer_lines=[["Text", [0, 0, 10, 10]]], boxes=[],
        is_diagram=False,
    )
    old_full, old_text = _cache_contexts(ConversionRequest(
        pdf=pdf, output_dir=tmp_path / "out", model_name="model-a",
        ocr_prompt="prompt-a"))
    new_full, new_text = _cache_contexts(ConversionRequest(
        pdf=pdf, output_dir=tmp_path / "out", model_name="model-b",
        ocr_prompt="prompt-b"))

    assert _page_cache_key(
        page, 1, old_full, old_text) == _page_cache_key(
        page, 1, new_full, new_text)
    assert page_cache.page_key(old_full, 1) != page_cache.page_key(
        new_full, 1)

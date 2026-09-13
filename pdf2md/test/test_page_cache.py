"""Unit tests for the resumable per-page cache (Issue #11)."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import fitz

from pdf2md import TILE_THRESHOLD, cache_parameters
from page_cache import PageCache, document_key


REPOSITORY = Path(__file__).resolve().parent.parent.parent


def _result(source="ocr"):
    return {
        "source": source,
        "chars": 123,
        "layout_type": "single-column",
        "gutter": None,
        "boxes": [[10, 20, 30, 40]],
        "diagram": False,
        "lines": [["Example", [10, 20, 30, 40]]],
        "trace": ["retried at finer resolution"],
        "mode": "whole page",
    }


def test_round_trip(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"pdf contents")
    cache = PageCache(tmp_path / "out", pdf, {"dpi": 150})

    cache.store(3, _result())

    assert cache.load(3) == _result()
    assert cache.path_for(3) == tmp_path / "out" / ".cache" / "source" / "0003.json"


def test_pdf_content_and_parameters_are_part_of_key(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"first")
    first = document_key(pdf, {"dpi": 150})
    different_parameter = document_key(pdf, {"dpi": 300})
    pdf.write_bytes(b"second")
    different_content = document_key(pdf, {"dpi": 150})

    assert len({first, different_parameter, different_content}) == 3


def test_key_mismatch_is_a_cache_miss(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"pdf contents")
    output = tmp_path / "out"
    PageCache(output, pdf, {"dpi": 150}).store(1, _result())

    assert PageCache(output, pdf, {"dpi": 300}).load(1) is None


def test_corrupt_or_incomplete_records_are_cache_misses(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"pdf contents")
    cache = PageCache(tmp_path / "out", pdf, {"dpi": 150})
    cache.directory.mkdir(parents=True)
    cache.path_for(1).write_text("not json", encoding="utf-8")
    assert cache.load(1) is None

    cache.path_for(1).write_text(
        json.dumps({
            "cache_version": 1,
            "document_key": cache.key,
            "page": 1,
            "result": {"source": "ocr"},
        }),
        encoding="utf-8",
    )
    assert cache.load(1) is None

    invalid = _result()
    invalid["chars"] = "123"
    cache.path_for(1).write_text(
        json.dumps({
            "cache_version": 1,
            "document_key": cache.key,
            "page": 1,
            "result": invalid,
        }),
        encoding="utf-8",
    )
    assert cache.load(1) is None


def _make_vector_pdf(path, pages=2):
    document = fitz.open()
    for page_number in range(1, pages + 1):
        page = document.new_page(width=600, height=800)
        page.insert_text(
            fitz.Point(10, 100),
            f"Page {page_number} " + "x" * 200,
            fontsize=5,
        )
    document.save(path)
    document.close()


def _run(pdf, output, *arguments):
    return subprocess.run(
        [
            sys.executable,
            "pdf2md/pdf2md.py",
            str(pdf),
            "--out",
            str(output),
            "--no-dictionary",
            *arguments,
        ],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_second_cli_run_reads_cached_pages(tmp_path):
    pdf = tmp_path / "source.pdf"
    output = tmp_path / "output"
    _make_vector_pdf(pdf)

    first = _run(pdf, output)
    second = _run(pdf, output)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "0 cached" in first.stdout
    assert "2 cached" in second.stdout
    assert second.stdout.count("cache: textlayer") == 2
    assert len(list((output / ".cache" / "source").glob("*.json"))) == 2


def test_changed_parameters_and_new_flag_recompute_pages(tmp_path):
    pdf = tmp_path / "source.pdf"
    output = tmp_path / "output"
    _make_vector_pdf(pdf)
    assert _run(pdf, output).returncode == 0

    changed = _run(pdf, output, "--dpi", "300")
    rebuilt = _run(pdf, output, "--new", "--pages", "2")

    assert changed.returncode == 0, changed.stderr
    assert "0 cached" in changed.stdout
    assert rebuilt.returncode == 0, rebuilt.stderr
    assert "0 cached" in rebuilt.stdout
    assert "%% S. 1 " not in (output / "source.md").read_text(encoding="utf-8")
    assert "%% S. 2 " in (output / "source.md").read_text(encoding="utf-8")


def test_cached_ocr_page_does_not_load_model(tmp_path):
    pdf = tmp_path / "scan.pdf"
    output = tmp_path / "output"
    document = fitz.open()
    document.new_page(width=600, height=800)
    document.save(pdf)
    document.close()

    args = SimpleNamespace(
        dpi=150,
        tile_from=TILE_THRESHOLD,
        no_bold=False,
        ocr_only=False,
        retries=1,
    )
    cache = PageCache(output, pdf, cache_parameters(args))
    cached = _result()
    cached.update({
        "chars": 0,
        "layout_type": "einspaltig",
        "boxes": [],
        "lines": [["Cached OCR text.", [10, 20, 300, 40]]],
        "trace": [],
        "mode": "ganz",
    })
    cache.store(1, cached)

    result = _run(pdf, output)

    assert result.returncode == 0, result.stderr
    assert "1 cached" in result.stdout
    assert "Cached OCR text." in (output / "scan.md").read_text(encoding="utf-8")

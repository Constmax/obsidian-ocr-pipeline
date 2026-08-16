"""Integration test for --pages page selection.

Run with: python3 -m pytest pdf2md/test/test_seiten.py -q
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import fitz  # PyMuPDF


def _make_vector_pdf(path: Path, pages: int = 4) -> None:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=600, height=800)
        page.insert_text(
            fitz.Point(10, 780 - i * 200),
            f"Seite {i + 1} — " + "x" * 180,
            fontsize=5,
        )
    doc.save(str(path))
    doc.close()


def _run_pdf2md(pdf_path: Path, out_dir: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    args = [
        sys.executable,
        "pdf2md/pdf2md.py",
        str(pdf_path),
        "--out",
        str(out_dir),
        "--fortschritt",
    ]
    if extra_args:
        args.extend(extra_args)
    return subprocess.run(
        args,
        cwd=str(Path(__file__).parent.parent.parent),
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_pages_selection():
    """ --pages "1,3" on 4-page PDF: only p.1 and p.3 in output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "test.pdf"
        _make_vector_pdf(pdf_path, pages=4)
        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        result = _run_pdf2md(pdf_path, out_dir, ["--seiten", "1,3"])
        assert result.returncode == 0, (
            f"pdf2md exited with code {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        md = (out_dir / "test.md").read_text()
        assert "%% S. 1 " in md, "Marker S. 1 missing"
        assert "%% S. 3 " in md, "Marker S. 3 missing"
        assert "%% S. 2 " not in md, "Marker S. 2 should not be present"
        assert "%% S. 4 " not in md, "Marker S. 4 should not be present"

        assert "seiten: 2" in md, f"Frontmatter pages should be 2:\n{md[:500]}"


def test_pages_range():
    """ --pages "2-4" on 4-page PDF: pages 2,3,4."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "test.pdf"
        _make_vector_pdf(pdf_path, pages=4)
        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        result = _run_pdf2md(pdf_path, out_dir, ["--seiten", "2-4"])
        assert result.returncode == 0, (
            f"pdf2md exited with code {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        md = (out_dir / "test.md").read_text()
        assert "%% S. 1 " not in md
        assert "%% S. 2 " in md
        assert "%% S. 3 " in md
        assert "%% S. 4 " in md
        assert "seiten: 3" in md


def test_pages_all():
    """Without --pages: all 4 pages."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "test.pdf"
        _make_vector_pdf(pdf_path, pages=4)
        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        result = _run_pdf2md(pdf_path, out_dir)
        assert result.returncode == 0

        md = (out_dir / "test.md").read_text()
        for i in range(1, 5):
            assert f"%% S. {i} " in md
        assert "seiten: 4" in md


def test_pages_invalid():
    """ --pages with valid input but out of range: Error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "test.pdf"
        _make_vector_pdf(pdf_path, pages=4)
        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        result = _run_pdf2md(pdf_path, out_dir, ["--seiten", "99"])
        assert result.returncode != 0
        assert "exist" in result.stderr or "existieren nicht" in result.stderr or "exist" in result.stdout or "existieren nicht" in result.stdout


def test_pages_progress_selection_only():
    """ --pages: Progress only for selected pages."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "test.pdf"
        _make_vector_pdf(pdf_path, pages=4)
        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        result = _run_pdf2md(pdf_path, out_dir, ["--seiten", "2,4"])
        assert result.returncode == 0

        stderr_lines = [l for l in result.stderr.splitlines() if l.strip()]
        page_lines = [l for l in stderr_lines if '"typ": "seite"' in l]
        page_nrs = {json.loads(l)["nr"] for l in page_lines}
        assert page_nrs == {2, 4}, f"Expected pages 2,4 in progress, got: {page_nrs}"

"""Integration test for --progress JSON progress emission.

Run with: python3 -m pytest pdf2md/test/test_fortschritt.py -q
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import fitz  # PyMuPDF


def _make_vector_pdf(path: Path, pages: int = 3) -> None:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=600, height=800)
        page.insert_text(
            fitz.Point(10, 780 - i * 200),
            "x" * 200,
            fontsize=5,
        )
    doc.save(str(path))
    doc.close()


def test_progress_vector_pdf():
    """Run over a vector PDF with --progress: exactly start, n page, finished."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "vector-test.pdf"
        _make_vector_pdf(pdf_path, pages=3)

        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        result = subprocess.run(
            [
                sys.executable,
                "pdf2md/pdf2md.py",
                str(pdf_path),
                "--fortschritt",
                "--out",
                str(out_dir),
            ],
            cwd=str(Path(__file__).parent.parent.parent),
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0, (
            f"pdf2md exited with code {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        stderr_lines = [l for l in result.stderr.splitlines() if l.strip()]

        start_lines = [l for l in stderr_lines if l.strip().startswith("{") and '"typ": "start"' in l]
        assert len(start_lines) == 1, (
            f"Expected exactly 1 start line, got: {start_lines}"
        )
        start_event = json.loads(start_lines[0])
        assert start_event["typ"] == "start"
        assert start_event["datei"] == "vector-test.pdf"
        assert start_event["seiten"] == 3
        assert start_event["dpi"] == 150

        page_lines = [l for l in stderr_lines if l.strip().startswith("{") and '"typ": "seite"' in l]
        assert len(page_lines) == 3, (
            f"Expected exactly 3 page lines, got: {page_lines}"
        )
        page_nrs = {json.loads(l)["nr"] for l in page_lines}
        assert page_nrs == {1, 2, 3}, f"Page numbers should be 1-3, got: {page_nrs}"
        for l in page_lines:
            e = json.loads(l)
            assert e["von"] == 3, f"Expected von=3, got {e['von']}"
            assert e["entgleist"] == False, f"Expected entgleist=False, got {e['entgleist']}"
            assert e["herkunft"] in ("textlayer", "ocr", "diagramm"), (
                f"Unexpected herkunft: {e['herkunft']}"
            )

        finished_lines = [l for l in stderr_lines if l.strip().startswith("{") and '"typ": "fertig"' in l]
        assert len(finished_lines) == 1, (
            f"Expected exactly 1 finished line, got: {finished_lines}"
        )
        finished_event = json.loads(finished_lines[0])
        assert finished_event["typ"] == "fertig"
        assert finished_event["entgleist"] == 0
        assert "sekunden" in finished_event
        assert "ziel" in finished_event

        all_json = [json.loads(l) for l in stderr_lines if l.strip().startswith("{")]
        types = {e["typ"] for e in all_json}
        assert types == {"start", "seite", "fertig"}, (
            f"Expected types {{'start', 'seite', 'fertig'}}, got {types}"
        )
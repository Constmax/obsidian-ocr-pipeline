"""Integration test for --seiten page selection.

Run with: python3 -m pytest pdf2md/test/test_seiten.py -q
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import fitz  # PyMuPDF


def _make_vektor_pdf(path: Path, seiten: int = 4) -> None:
    doc = fitz.open()
    for i in range(seiten):
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


def test_seiten_auswahl():
    """ --seiten "1,3" auf 4-seitigem PDF: nur S.1 und S.3 im Output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "test.pdf"
        _make_vektor_pdf(pdf_path, seiten=4)
        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        ergebnis = _run_pdf2md(pdf_path, out_dir, ["--seiten", "1,3"])
        assert ergebnis.returncode == 0, (
            f"pdf2md exited with code {ergebnis.returncode}\n"
            f"stdout: {ergebnis.stdout}\nstderr: {ergebnis.stderr}"
        )

        md = (out_dir / "test.md").read_text()
        # Markers fuer S.1 und S.3 muessen vorhanden sein (mit Suffix wie "textlayer")
        assert "%% S. 1 " in md, "Marker S. 1 fehlt"
        assert "%% S. 3 " in md, "Marker S. 3 fehlt"
        # Markers fuer S.2 und S.4 duerfen NICHT vorhanden sein
        assert "%% S. 2 " not in md, "Marker S. 2 sollte nicht vorhanden sein"
        assert "%% S. 4 " not in md, "Marker S. 4 sollte nicht vorhanden sein"

        # Frontmatter seiten = 2
        assert "seiten: 2" in md, f"Frontmatter seiten sollte 2 sein:\n{md[:500]}"


def test_seiten_bereich():
    """ --seiten "2-4" auf 4-seitigem PDF: Seiten 2,3,4."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "test.pdf"
        _make_vektor_pdf(pdf_path, seiten=4)
        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        ergebnis = _run_pdf2md(pdf_path, out_dir, ["--seiten", "2-4"])
        assert ergebnis.returncode == 0, (
            f"pdf2md exited with code {ergebnis.returncode}\n"
            f"stdout: {ergebnis.stdout}\nstderr: {ergebnis.stderr}"
        )

        md = (out_dir / "test.md").read_text()
        assert "%% S. 1 " not in md, "Marker S. 1 sollte nicht vorhanden sein"
        assert "%% S. 2 " in md
        assert "%% S. 3 " in md
        assert "%% S. 4 " in md
        assert "seiten: 3" in md


def test_seiten_alle():
    """Ohne --seiten: alle 4 Seiten."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "test.pdf"
        _make_vektor_pdf(pdf_path, seiten=4)
        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        ergebnis = _run_pdf2md(pdf_path, out_dir)
        assert ergebnis.returncode == 0

        md = (out_dir / "test.md").read_text()
        for i in range(1, 5):
            assert f"%% S. {i} " in md
        assert "seiten: 4" in md


def test_seiten_fehlerhaft():
    """ --seiten mit gueltiger Eingabe aber ausserhalb des Bereichs: Fehler."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "test.pdf"
        _make_vektor_pdf(pdf_path, seiten=4)
        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        ergebnis = _run_pdf2md(pdf_path, out_dir, ["--seiten", "99"])
        assert ergebnis.returncode != 0, "Erwartet Fehler-Code bei gueltiger aber ausserhalb-Angabe"
        assert "existieren nicht" in ergebnis.stderr or "existieren nicht" in ergebnis.stdout


def test_seiten_fortschritt_nur_auswahl():
    """ --seiten: Fortschritt nur fuer die gewaehlten Seiten."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "test.pdf"
        _make_vektor_pdf(pdf_path, seiten=4)
        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        ergebnis = _run_pdf2md(pdf_path, out_dir, ["--seiten", "2,4"])
        assert ergebnis.returncode == 0

        stderr_lines = [l for l in ergebnis.stderr.splitlines() if l.strip()]
        seite_lines = [l for l in stderr_lines if '"typ": "seite"' in l]
        seite_nrs = {json.loads(l)["nr"] for l in seite_lines}
        assert seite_nrs == {2, 4}, f"Erwartet Seiten 2,4 im Fortschritt, erhalten: {seite_nrs}"

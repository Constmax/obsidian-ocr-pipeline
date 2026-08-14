"""Integration test for --fortschritt JSON progress emission.

Run with: python3 -m pytest pdf2md/test/test_fortschritt.py -q
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import fitz  # PyMuPDF


def _make_vektor_pdf(path: Path, seiten: int = 3) -> None:
    doc = fitz.open()
    for i in range(seiten):
        page = doc.new_page(width=600, height=800)
        # Viele Wiederholungen des Buchstaben "x" in kleinster Schrift,
        # damit chars >= 100, die gerenderte Fläche aber klein bleibt.
        page.insert_text(
            fitz.Point(10, 780 - i * 200),
            "x" * 200,                       # 200 Zeichen => chars >= 100
            fontsize=5,
        )
    doc.save(str(path))
    doc.close()


def test_fortschritt_vektor_pdf():
    """Lauf ueber eine Vektor-PDF mit --fortschritt: genau start, n seite, fertig."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "vektor-test.pdf"
        _make_vektor_pdf(pdf_path, seiten=3)

        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        Ergebnis = subprocess.run(
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

        assert Ergebnis.returncode == 0, (
            f"pdf2md exited with code {Ergebnis.returncode}\n"
            f"stdout: {Ergebnis.stdout}\nstderr: {Ergebnis.stderr}"
        )

        stderr_lines = [l for l in Ergebnis.stderr.splitlines() if l.strip()]

        # Exakt eine start-Zeile
        start_lines = [l for l in stderr_lines if l.strip().startswith("{") and '"typ": "start"' in l]
        assert len(start_lines) == 1, (
            f"Erwartet genau 1 start-Zeile, erhalten: {start_lines}"
        )
        start_ereignis = json.loads(start_lines[0])
        assert start_ereignis["typ"] == "start"
        assert start_ereignis["datei"] == "vektor-test.pdf"
        assert start_ereignis["seiten"] == 3
        assert start_ereignis["dpi"] == 150

        # Exakt n seite-Zeilen (hier 3, ein pro Seite)
        seite_lines = [l for l in stderr_lines if l.strip().startswith("{") and '"typ": "seite"' in l]
        assert len(seite_lines) == 3, (
            f"Erwartet genau 3 seite-Zeilen, erhalten: {seite_lines}"
        )
        # Jede Seite einmal, nr von 1-3
        seite_nrs = {json.loads(l)["nr"] for l in seite_lines}
        assert seite_nrs == {1, 2, 3}, f"Seiten-NRs sollten 1-3 sein, erhalten: {seite_nrs}"
        # Alle haben von=3 und entgleist=false (Textlayer-Weg, kein spur)
        for l in seite_lines:
            e = json.loads(l)
            assert e["von"] == 3, f"Expected von=3, got {e['von']}"
            assert e["entgleist"] == False, f"Expected entgleist=False, got {e['entgleist']}"
            assert e["herkunft"] in ("textlayer", "ocr", "diagramm"), (
                f"Unerwartete herkunft: {e['herkunft']}"
            )

        # Exakt eine fertig-Zeile
        fertig_lines = [l for l in stderr_lines if l.strip().startswith("{") and '"typ": "fertig"' in l]
        assert len(fertig_lines) == 1, (
            f"Erwartet genau 1 fertig-Zeile, erhalten: {fertig_lines}"
        )
        fertig_ereignis = json.loads(fertig_lines[0])
        assert fertig_ereignis["typ"] == "fertig"
        assert fertig_ereignis["entgleist"] == 0  # n_entgleist as int
        assert "sekunden" in fertig_ereignis
        assert "ziel" in fertig_ereignis

        # Keine weiteren JSON-Ereignisse
        alle_json = [json.loads(l) for l in stderr_lines if l.strip().startswith("{")]
        typen = {e["typ"] for e in alle_json}
        # Sollte start, seite und fertig enthalten (typ ist immer "seite",
        # die Seiten-Nr liegt im Feld "nr")
        assert typen == {"start", "seite", "fertig"}, (
            f"Erwartete Typen {{'start', 'seite', 'fertig'}}, erhalten {typen}"
        )
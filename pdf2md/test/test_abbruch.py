#!/usr/bin/env python3
"""Tests fuer das Abbruch-Modul (SIGINT/SIGTERM, Issue #25).

Laeuft ohne MLX und fitz: nur das Modul abbruch.py wird importiert.
"""
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

import abbruch


@pytest.fixture
def handler():
    """Handlers installieren, nach dem Test Flag und Handlers zuruecksetzen."""
    abbruch.installieren()
    yield
    abbruch.zuruecksetzen()
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


def _signal(sig):
    os.kill(os.getpid(), sig)


def test_erstes_signal_setzt_nur_das_flag(handler):
    _signal(signal.SIGINT)

    assert abbruch.angefordert() is True


def test_erstes_sigterm_setzt_das_flag(handler):
    _signal(signal.SIGTERM)

    assert abbruch.angefordert() is True


def test_zweites_signal_beendet_mit_code_6(handler):
    _signal(signal.SIGINT)

    with pytest.raises(SystemExit) as info:
        _signal(signal.SIGINT)

    assert info.value.code == 6


def test_ohne_signal_kein_abbruch(handler):
    assert abbruch.angefordert() is False


def test_zuruecksetzen_macht_erneut_abbruchbar(handler):
    _signal(signal.SIGINT)
    abbruch.zuruecksetzen()
    assert abbruch.angefordert() is False

    _signal(signal.SIGINT)
    assert abbruch.angefordert() is True


def _make_vektor_pdf(path: Path, seiten: int = 50) -> None:
    import fitz
    doc = fitz.open()
    for i in range(seiten):
        page = doc.new_page(width=600, height=800)
        # Zeile oben auf der Seite (nicht bei jeder Seite weiter nach unten:
        # fuer i >= 4 fiele die Position aus dem Seitenbereich und die Seite
        # waere leer → Scan-Erkennung statt Textlayer).
        page.insert_text(
            fitz.Point(10, 100),
            "x" * 200,                       # 200 Zeichen => chars >= 100
            fontsize=5,
        )
    doc.save(str(path))
    doc.close()


def test_sigterm_vor_erster_seite_keine_teildatei():
    """Issue #25: SIGTERM waehrend der Analyse (vor der ersten Seite) ergibt
    Exit-Code 7 und keine Datei — der Aufrufer darf keine Teildatei melden."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "abbruch-frueh.pdf"
        _make_vektor_pdf(pdf_path, seiten=50)

        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        proc = subprocess.Popen(
            [
                sys.executable,
                "-u",                      # stdout ungepuffert: die Analysiere-Zeile
                                           # kommt sofort an, der Seitenlauf ist noch
                                           # nicht angelaufen
                "pdf2md/pdf2md.py",
                str(pdf_path),
                "--fortschritt",
                "--out",
                str(out_dir),
            ],
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Die Analysiere-Zeile steht nach der Handler-Installation, aber vor
        # der ersten Seite: das Signal trifft also sicher vor dem Seitenlauf
        # ein (kein Teildatei-Versuch).
        analyse = threading.Event()
        stdout_zeilen: list[str] = []

        def stdout_lesen():
            for zeile in proc.stdout:
                stdout_zeilen.append(zeile)
                if zeile.startswith("Analysiere"):
                    analyse.set()

        thread = threading.Thread(target=stdout_lesen, daemon=True)
        thread.start()
        assert analyse.wait(timeout=120), "keine Analysiere-Zeile — Lauf haengt?"
        os.kill(proc.pid, signal.SIGTERM)
        proc.wait(timeout=120)
        stderr = proc.stderr.read() if proc.stderr else ""
        stdout = "".join(stdout_zeilen)

        assert proc.returncode == 7, (
            f"Exit-Code {proc.returncode} statt 7\nstdout: {stdout}\n"
            f"stderr: {stderr}"
        )
        assert "keine Teildatei" in stdout, (
            f"Hinweis auf fehlende Teildatei fehlt\nstdout: {stdout}"
        )
        ziel = out_dir / "abbruch-frueh.md"
        assert not ziel.exists(), "Datei darf vor der ersten Seite nicht entstehen"
        assert not list(out_dir.glob("_tmp-*"))
        assert not list(
            (Path(__file__).resolve().parent.parent / "out-C").glob("_tmp-*")
        ), "Temporaerordner liegt noch unter pdf2md/out-C"


def test_sigterm_waehrend_letzter_seite_vollstaendig():
    """Issue #25: SIGTERM waehrend der letzten Seite ergibt keine Teildatei:
    die Datei ist vollstaendig (Exit-Code 0, fertig-Ereignis, kein
    abgebrochen-Vermerk)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "abbruch-spaet.pdf"
        _make_vektor_pdf(pdf_path, seiten=50)

        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        proc = subprocess.Popen(
            [
                sys.executable,
                "pdf2md/pdf2md.py",
                str(pdf_path),
                "--fortschritt",
                "--diagramm-seiten",
                "50",                      # Seite 50 rendert ein PNG: das Signal
                                           # trifft sicher in ihre Verarbeitung
                "--out",
                str(out_dir),
            ],
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Nach der Seite 49 SIGTERM: die letzte Seite ist gerade in Arbeit,
        # das Signal kommt vor ihrem Abschluss an.
        vorletzte = threading.Event()
        stderr_zeilen: list[str] = []

        def stderr_lesen():
            for zeile in proc.stderr:
                stderr_zeilen.append(zeile)
                if '"typ": "seite", "nr": 49' in zeile:
                    vorletzte.set()

        thread = threading.Thread(target=stderr_lesen, daemon=True)
        thread.start()
        assert vorletzte.wait(timeout=120), "kein Seiten-Ereignis 49 — Lauf haengt?"
        os.kill(proc.pid, signal.SIGTERM)
        proc.wait(timeout=120)
        stdout = proc.stdout.read() if proc.stdout else ""
        stderr = "".join(stderr_zeilen)

        assert proc.returncode == 0, (
            f"Exit-Code {proc.returncode} statt 0\nstdout: {stdout}\n"
            f"stderr: {stderr}"
        )
        ziel = out_dir / "abbruch-spaet.md"
        assert ziel.exists(), "Datei fehlt trotz vollstaendigem Lauf"
        text = ziel.read_text(encoding="utf-8")
        assert "abgebrochen" not in text, (
            f"abgebrochen-Vermerk in vollstaendiger Datei:\n{text}"
        )
        assert '"typ": "fertig"' in stderr, (
            "fertig-Ereignis fehlt nach abgeschlossener letzter Seite"
        )


def test_sigint_halb_durch_den_lauf():
    """Issue #25: SIGINT mitten im Lauf ergibt Exit-Code 6, eine Teildatei
    mit abgebrochen-Vermerk und keine liegengebliebenen _tmp-Ordner."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "abbruch-test.pdf"
        _make_vektor_pdf(pdf_path, seiten=50)

        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        proc = subprocess.Popen(
            [
                sys.executable,
                "pdf2md/pdf2md.py",
                str(pdf_path),
                "--fortschritt",
                "--out",
                str(out_dir),
            ],
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Beim start-Ereignis (kurz vor dem Seitenlauf) SIGINT senden: der
        # Lauf bricht dann mitten in den Seiten ab.
        start = threading.Event()
        stderr_zeilen: list[str] = []

        def stderr_lesen():
            for zeile in proc.stderr:
                stderr_zeilen.append(zeile)
                if '"typ": "start"' in zeile:
                    start.set()

        thread = threading.Thread(target=stderr_lesen, daemon=True)
        thread.start()
        assert start.wait(timeout=120), "kein start-Ereignis — Lauf haengt?"
        os.kill(proc.pid, signal.SIGINT)
        proc.wait(timeout=120)
        stdout = proc.stdout.read() if proc.stdout else ""

        assert proc.returncode == 6, (
            f"Exit-Code {proc.returncode} statt 6\nstdout: {stdout}\n"
            f"stderr: {''.join(stderr_zeilen)}"
        )
        ziel = out_dir / "abbruch-test.md"
        assert ziel.exists(), "Teildatei fehlt nach Abbruch"
        text = ziel.read_text(encoding="utf-8")
        treffer = re.search(r"abgebrochen: seite (\d+) von 50", text)
        assert treffer is not None, f"abgebrochen-Vermerk fehlt:\n{text}"
        assert int(treffer.group(1)) >= 1
        # Kein fertig-Ereignis: der Lauf wurde abgebrochen, nicht beendet.
        assert '"typ": "fertig"' not in "".join(stderr_zeilen)
        # Zwischenbilder sind aufgeraeumt — die Temp-Verzeichnisse liegen per
        # Konvention unter pdf2md/out-C, nicht im --out-Ziel.
        assert not list(out_dir.glob("_tmp-*"))
        assert not list(
            (Path(__file__).resolve().parent.parent / "out-C").glob("_tmp-*")
        ), "Temporaerordner liegt noch unter pdf2md/out-C"

#!/usr/bin/env python3
"""Tests for the cancellation module (SIGINT/SIGTERM, Issue #25).

Runs without MLX and fitz: only abbruch.py module is imported.
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

import cancellation


@pytest.fixture
def handler():
    """Install handlers, reset flag and handlers after test."""
    cancellation.install()
    yield
    cancellation.reset()
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


def _signal(sig):
    os.kill(os.getpid(), sig)


def test_first_signal_sets_only_the_flag(handler):
    _signal(signal.SIGINT)

    assert cancellation.requested() is True


def test_first_sigterm_sets_the_flag(handler):
    _signal(signal.SIGTERM)

    assert cancellation.requested() is True


def test_second_signal_exits_with_code_6(handler):
    _signal(signal.SIGINT)

    with pytest.raises(SystemExit) as info:
        _signal(signal.SIGINT)

    assert info.value.code == 6


def test_without_signal_no_cancellation(handler):
    assert cancellation.requested() is False


def test_reset_makes_cancellable_again(handler):
    _signal(signal.SIGINT)
    cancellation.reset()
    assert cancellation.requested() is False

    _signal(signal.SIGINT)
    assert cancellation.requested() is True


def _make_vector_pdf(path: Path, pages: int = 50) -> None:
    import fitz
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=600, height=800)
        page.insert_text(
            fitz.Point(10, 100),
            "x" * 200,
            fontsize=5,
        )
    doc.save(str(path))
    doc.close()


def test_sigterm_before_first_page_no_partial_file():
    """Issue #25: SIGTERM during analysis (before first page) yields exit code 7 and no file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "cancellation-early.pdf"
        _make_vector_pdf(pdf_path, pages=50)

        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        proc = subprocess.Popen(
            [
                sys.executable,
                "-u",
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

        analysis = threading.Event()
        stdout_lines: list[str] = []

        def read_stdout():
            for line in proc.stdout:
                stdout_lines.append(line)
                if line.startswith("Analyzing") or line.startswith("Analysiere"):
                    analysis.set()

        thread = threading.Thread(target=read_stdout, daemon=True)
        thread.start()
        assert analysis.wait(timeout=120), "no Analyzing line — run hanging?"
        os.kill(proc.pid, signal.SIGTERM)
        proc.wait(timeout=120)
        stderr = proc.stderr.read() if proc.stderr else ""
        stdout = "".join(stdout_lines)

        assert proc.returncode == 7, (
            f"Exit code {proc.returncode} instead of 7\nstdout: {stdout}\n"
            f"stderr: {stderr}"
        )
        assert "no partial file" in stdout or "keine Teildatei" in stdout, (
            f"Missing partial file note\nstdout: {stdout}"
        )
        target = out_dir / "cancellation-early.md"
        assert not target.exists(), "File must not be created before first page"
        assert not list(out_dir.glob("_tmp-*"))
        assert not list(
            (Path(__file__).resolve().parent.parent / "out-C").glob("_tmp-*")
        ), "Temp folder still under pdf2md/out-C"


def test_sigterm_during_last_page_complete():
    """Issue #25: SIGTERM during last page yields complete file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "cancellation-late.pdf"
        _make_vector_pdf(pdf_path, pages=50)

        out_dir = Path(tmpdir) / "out"
        out_dir.mkdir()

        proc = subprocess.Popen(
            [
                sys.executable,
                "pdf2md/pdf2md.py",
                str(pdf_path),
                "--fortschritt",
                "--diagramm-seiten",
                "50",
                "--out",
                str(out_dir),
            ],
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        penultimate = threading.Event()
        stderr_lines: list[str] = []

        def read_stderr():
            for line in proc.stderr:
                stderr_lines.append(line)
                if '"typ": "seite", "nr": 49' in line:
                    penultimate.set()

        thread = threading.Thread(target=read_stderr, daemon=True)
        thread.start()
        assert penultimate.wait(timeout=120), "no page event 49 — run hanging?"
        os.kill(proc.pid, signal.SIGTERM)
        proc.wait(timeout=120)
        stdout = proc.stdout.read() if proc.stdout else ""
        stderr = "".join(stderr_lines)

        assert proc.returncode == 0, (
            f"Exit code {proc.returncode} instead of 0\nstdout: {stdout}\n"
            f"stderr: {stderr}"
        )
        target = out_dir / "cancellation-late.md"
        assert target.exists(), "File missing despite full run"
        text = target.read_text(encoding="utf-8")
        assert "abgebrochen" not in text, (
            f"Aborted note in complete file:\n{text}"
        )
        assert '"typ": "fertig"' in stderr, (
            "Fertig event missing after completed last page"
        )


def test_sigint_halfway_through_run():
    """Issue #25: SIGINT mid-run yields exit code 6, a partial file with aborted note."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "cancellation-test.pdf"
        _make_vector_pdf(pdf_path, pages=50)

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

        start = threading.Event()
        stderr_lines: list[str] = []

        def read_stderr():
            for line in proc.stderr:
                stderr_lines.append(line)
                if '"typ": "start"' in line:
                    start.set()

        thread = threading.Thread(target=read_stderr, daemon=True)
        thread.start()
        assert start.wait(timeout=120), "no start event — run hanging?"
        os.kill(proc.pid, signal.SIGINT)
        proc.wait(timeout=120)
        stdout = proc.stdout.read() if proc.stdout else ""

        assert proc.returncode == 6, (
            f"Exit code {proc.returncode} instead of 6\nstdout: {stdout}\n"
            f"stderr: {''.join(stderr_lines)}"
        )
        target = out_dir / "cancellation-test.md"
        assert target.exists(), "Partial file missing after cancellation"
        text = target.read_text(encoding="utf-8")
        match = re.search(r"abgebrochen: seite (\d+) von 50", text)
        assert match is not None, f"Aborted note missing:\n{text}"
        assert int(match.group(1)) >= 1
        assert '"typ": "fertig"' not in "".join(stderr_lines)
        assert not list(out_dir.glob("_tmp-*"))
        assert not list(
            (Path(__file__).resolve().parent.parent / "out-C").glob("_tmp-*")
        ), "Temp folder still under pdf2md/out-C"

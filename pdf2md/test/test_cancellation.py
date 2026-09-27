#!/usr/bin/env python3
"""Tests for the cancellation module (SIGINT/SIGTERM, Issue #25).

The handler tests run without MLX; the CLI tests replace the model with a
fake adapter.
"""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

import cancellation
import pdf2md as pdf2md_cli
from conversion import ConversionRequest, convert_document


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


def test_second_signal_raises_interrupted(handler):
    """Issue #105: the exit code is decided by main, not by the handler."""
    _signal(signal.SIGINT)

    with pytest.raises(cancellation.Interrupted):
        _signal(signal.SIGINT)


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


@pytest.mark.slow
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


@pytest.mark.slow
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
                event = json.loads(line) if line.startswith("{") else {}
                if event.get("typ") == "seite" and event.get("nr") == 49:
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


@pytest.mark.slow
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

        cache_dir = out_dir / ".cache" / pdf_path.stem
        cached_before = sorted(p.name for p in cache_dir.glob("*.json"))
        assert cached_before, "Issue #11: no page cached before cancellation"

        resumed = subprocess.run(
            [
                sys.executable,
                "pdf2md/pdf2md.py",
                str(pdf_path),
                "--out",
                str(out_dir),
            ],
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert resumed.returncode == 0, (
            f"resume failed\nstdout: {resumed.stdout}\nstderr: {resumed.stderr}"
        )
        assert f"cache: {len(cached_before)} page(s) reused" in resumed.stdout, (
            "Issue #11: resume did not reuse the pages cached before cancellation\n"
            f"stdout: {resumed.stdout}"
        )
        resumed_text = target.read_text(encoding="utf-8")
        assert "abgebrochen" not in resumed_text
        assert all(f"%% S. {n} " in resumed_text for n in range(1, 51))

        clean_out = Path(tmpdir) / "clean"
        clean = subprocess.run(
            [
                sys.executable,
                "pdf2md/pdf2md.py",
                str(pdf_path),
                "--out",
                str(clean_out),
            ],
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert clean.returncode == 0, clean.stderr
        clean_text = (clean_out / "cancellation-test.md").read_text(
            encoding="utf-8"
        )

        def without_run_time(value):
            return re.sub(
                r"^ocr-(?:datum|zeitpunkt):.*$", "", value, flags=re.MULTILINE,
            )

        assert without_run_time(resumed_text) == without_run_time(clean_text), (
            "Issue #11: resumed result differs from an uninterrupted run"
        )


def _run_cli(monkeypatch, adapter, *argv):
    """Run pdf2md's main() in-process with `adapter` in place of the model."""
    monkeypatch.setattr(pdf2md_cli, "LazyMlxOcrAdapter", lambda _model: adapter)
    monkeypatch.setattr(sys, "argv", ["pdf2md.py", *map(str, argv)])
    try:
        with pytest.raises(SystemExit) as info:
            pdf2md_cli.main()
    finally:
        cancellation.reset()
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    return info.value.code


class _SlowAdapter:
    """Stands in for the model. Page 2 takes far longer than the plugin's
    grace period, so the plugin's second SIGTERM arrives mid-call."""

    def prepare(self):
        pass

    def __call__(self, image, max_tokens=None):
        if Path(image).name.startswith("_seite002"):
            _signal(signal.SIGTERM)  # the user cancels
            threading.Timer(0.2, _signal, (signal.SIGTERM,)).start()
            time.sleep(10)
            raise AssertionError("the second SIGTERM did not stop the page")
        return "Text der ersten Seite"


@pytest.mark.slow
def test_second_sigterm_during_an_ocr_page_writes_the_partial_file(
        tmp_path, monkeypatch):
    """Issue #105: a cancel during a slow OCR page ends with exit 6 and a
    partial file holding exactly the pages finished before it."""
    pdf = tmp_path / "slow.pdf"
    _make_vector_pdf(pdf, pages=3)
    out = tmp_path / "out"

    code = _run_cli(monkeypatch, _SlowAdapter(), pdf, "--ocr-only",
                    "--kein-woerterbuch", "--out", out)

    assert code == 6
    text = (out / "slow.md").read_text(encoding="utf-8")
    assert "abgebrochen: seite 1 von 3" in text
    assert "%% S. 1 " in text
    assert "%% S. 2 " not in text
    assert not [path.name for path in out.iterdir() if path.suffix == ".tmp"]


def test_second_signal_before_the_first_page_exits_7(tmp_path, monkeypatch):
    """Issue #105: exit 6 promises a partial file, so a repeated signal
    before any page is finished must end with 7."""
    pdf = tmp_path / "early.pdf"
    _make_vector_pdf(pdf, pages=3)
    out = tmp_path / "out"

    class InterruptedWhileLoading:
        def prepare(self):
            _signal(signal.SIGTERM)
            _signal(signal.SIGTERM)

        def __call__(self, image, max_tokens=None):
            raise AssertionError("no page may start after the second signal")

    code = _run_cli(monkeypatch, InterruptedWhileLoading(), pdf, "--ocr-only",
                    "--kein-woerterbuch", "--out", out)

    assert code == 7
    assert not (out / "early.md").exists()


def test_interrupt_while_writing_keeps_the_previous_preview(
        tmp_path, monkeypatch):
    """Issue #105: a stop in the middle of the result write leaves the
    previous preview whole and no hidden temporary file behind."""
    pdf = tmp_path / "input.pdf"
    _make_vector_pdf(pdf, pages=2)
    out = tmp_path / "out"
    out.mkdir()
    previous = out / "input.md"
    previous.write_text("previous preview\n", encoding="utf-8")
    write_text = Path.write_text

    def half_then_interrupted(self, text, *args, **kwargs):
        if self.name.startswith(".input.md."):
            write_text(self, text[:len(text) // 2], *args, **kwargs)
            raise cancellation.Interrupted()
        return write_text(self, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", half_then_interrupted)

    with pytest.raises(cancellation.Interrupted):
        convert_document(ConversionRequest(pdf=pdf, output_dir=out), None)

    assert previous.read_text(encoding="utf-8") == "previous preview\n"
    assert sorted(path.name for path in out.iterdir()) == [".cache", "input.md"]

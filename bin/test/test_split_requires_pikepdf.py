"""Behavioral tests for issue #47: column splitting requires pikepdf.

Without pikepdf there is no split map, so split halves cannot be merged back
and the result silently keeps doubled half-pages. These tests pin that every
split path fails or is skipped instead of handing off such a result.

External tools (ocrmypdf, gs, qpdf, python3, ...) are replaced by stubs on
PATH, so the tests need neither ocrmypdf nor pikepdf and behave the same in
CI and locally.

Run with: python3 -m pytest bin/test -q
"""
import os
import subprocess
from pathlib import Path

import pytest


BIN = Path(__file__).resolve().parent.parent


def _stub(stub_dir, name, body):
    path = stub_dir / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


@pytest.fixture
def sandbox(tmp_path):
    """Stubbed toolchain without pikepdf; every tool call lands in calls.log."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "calls.log"
    # The Apple Vision plugin probe fails silently; every real OCR call is logged.
    _stub(stubs, "ocrmypdf", f'[ "$1" = "--plugin" ] && exit 1\necho "ocrmypdf $*" >> "{log}"\nexit 1\n')
    for tool in ("gs", "qpdf", "pdfinfo", "pdftotext", "img2pdf"):
        _stub(stubs, tool, f'echo "{tool} $*" >> "{log}"\nexit 1\n')
    # python3 without pikepdf: `python3 -c "import pikepdf"` fails.
    _stub(stubs, "python3", "exit 1\n")

    work = tmp_path / "work"
    work.mkdir()
    (work / "in.pdf").write_text("orig")
    env = dict(
        os.environ,
        PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
        VENV_ROOT=str(tmp_path / "no-venvs"),
    )
    return {"stubs": stubs, "log": log, "work": work, "env": env}


def _calls(sandbox):
    log = sandbox["log"]
    return log.read_text().splitlines() if log.exists() else []


def _run_lib(sandbox, script):
    """Source pdf-lib.sh the way the CLIs do and run a bash snippet."""
    return subprocess.run(
        ["bash", "-c",
         f'set -euo pipefail\nSCRIPT_DIR="{BIN}"\nsource "{BIN}/pdf-lib.sh"\n{script}'],
        cwd=sandbox["work"], env=sandbox["env"],
        capture_output=True, text=True, timeout=30,
    )


# ── CLI: fail before processing begins ─────────────────────────────────────

CLIS = [
    ("pdf-auto.sh", []),
    ("pdf-combine.sh", ["out"]),
    ("pdf-workflow.sh", ["out"]),
]


@pytest.mark.parametrize("flag", ["--split-columns", "--split-columns-all"])
@pytest.mark.parametrize("script,extra", CLIS, ids=[c[0] for c in CLIS])
def test_cli_split_without_pikepdf_fails_before_ocr(sandbox, script, extra, flag):
    result = subprocess.run(
        ["bash", str(BIN / script), str(sandbox["work"]), *extra, flag],
        env=sandbox["env"], capture_output=True, text=True, timeout=30,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    assert "pikepdf" in output, output
    # Nothing was merged, downscaled or OCR'd — the gate sits before processing.
    assert _calls(sandbox) == [], output
    assert sorted(p.name for p in sandbox["work"].iterdir()) == ["in.pdf"]


def test_cli_without_split_stays_usable_without_pikepdf(sandbox):
    result = subprocess.run(
        ["bash", str(BIN / "pdf-auto.sh"), str(sandbox["work"])],
        env=sandbox["env"], capture_output=True, text=True, timeout=30,
    )
    output = result.stdout + result.stderr

    assert "pikepdf not found — automatic column-split retry disabled" in output
    assert any(c.startswith("ocrmypdf ") for c in _calls(sandbox)), output
    # The automatic split retry must not run without pikepdf.
    assert "Retry with --split-columns" not in output


# ── split_two_column_pdf: no fallback without a map ────────────────────────

@pytest.mark.parametrize("python_bin", ["", "failing", "no-map"])
def test_split_without_map_fails_without_fallback(sandbox, python_bin):
    if python_bin == "failing":
        python_bin = str(sandbox["stubs"] / "python3")
    elif python_bin == "no-map":
        # column_tools.py "succeeds" and writes the PDF, but no split map.
        python_bin = str(_stub(sandbox["stubs"], "python-no-map", 'echo split > "$4"\nexit 0\n'))

    result = _run_lib(sandbox, f'''
PYTHON_BIN="{python_bin}"
rc=0; split_two_column_pdf in.pdf split.pdf || rc=$?
echo "rc=$rc map=[$SPLIT_MAP]"
''')

    assert result.returncode == 0, result.stderr
    assert "rc=1 map=[]" in result.stdout, result.stdout + result.stderr
    assert not (sandbox["work"] / "split.pdf").exists()
    # The deleted Ghostscript loop called gs and qpdf directly.
    assert _calls(sandbox) == []


# ── merge_split_pdf: never copies the split version through ────────────────

def test_merge_without_map_fails(sandbox):
    (sandbox["work"] / "ocr.pdf").write_text("split")
    result = _run_lib(sandbox, '''
SPLIT_MAP=""
rc=0; merge_split_pdf ocr.pdf merged.pdf || rc=$?
echo "rc=$rc"
''')

    assert "rc=1" in result.stdout, result.stdout + result.stderr
    assert not (sandbox["work"] / "merged.pdf").exists()


def test_merge_failure_fails(sandbox):
    (sandbox["work"] / "ocr.pdf").write_text("split")
    (sandbox["work"] / "map.json").write_text("{}")
    result = _run_lib(sandbox, f'''
PYTHON_BIN="{sandbox["stubs"] / "python3"}"
SPLIT_MAP=map.json
rc=0; merge_split_pdf ocr.pdf merged.pdf || rc=$?
echo "rc=$rc"
''')

    assert "rc=1" in result.stdout, result.stdout + result.stderr
    assert not (sandbox["work"] / "merged.pdf").exists()


def test_keep_split_still_copies_split_version(sandbox):
    (sandbox["work"] / "ocr.pdf").write_text("split")
    result = _run_lib(sandbox, '''
KEEP_SPLIT=true
rc=0; merge_split_pdf ocr.pdf merged.pdf || rc=$?
echo "rc=$rc"
''')

    assert "rc=0" in result.stdout, result.stdout + result.stderr
    assert (sandbox["work"] / "merged.pdf").read_text() == "split"


# ── ocr_with_retry: automatic split retry ──────────────────────────────────

# Attempt 1 always fails the quality gate; only the split variant passes.
RETRY_HARNESS = '''
USE_APPLE=false; SPLIT_COLUMNS=false; ENGINE_DESC="Tesseract"
WORK_DIR="$PWD"
ocr_args=(-l deu --deskew)
run_ocr() { echo "run_ocr $1" >> ocr.log; cp "$1" "$2"; }
quality_check() { [ "$(cat "$1")" = "split" ]; }
'''


def test_retry_skips_split_without_pikepdf(sandbox):
    result = _run_lib(sandbox, RETRY_HARNESS + '''
PYTHON_BIN=""
split_two_column_pdf() { echo "split called"; return 1; }
rc=0; ocr_with_retry in.pdf out.pdf "" ocr_args || rc=$?
echo "rc=$rc"
''')

    assert "rc=1" in result.stdout, result.stdout + result.stderr
    assert "split called" not in result.stdout
    assert (sandbox["work"] / "ocr.log").read_text().splitlines() == ["run_ocr in.pdf"]


def test_retry_skips_ocr_when_split_fails(sandbox):
    result = _run_lib(sandbox, RETRY_HARNESS + '''
PYTHON_BIN=python3
split_two_column_pdf() { return 1; }
rc=0; ocr_with_retry in.pdf out.pdf "" ocr_args || rc=$?
echo "rc=$rc"
''')

    assert "rc=1" in result.stdout, result.stdout + result.stderr
    assert (sandbox["work"] / "ocr.log").read_text().splitlines() == ["run_ocr in.pdf"]


def test_retry_discards_unmergeable_split_result(sandbox):
    result = _run_lib(sandbox, RETRY_HARNESS + '''
PYTHON_BIN=python3
split_two_column_pdf() { echo split > "$2"; }
merge_split_pdf() { return 1; }
rc=0; ocr_with_retry in.pdf out.pdf "" ocr_args || rc=$?
echo "rc=$rc"
''')

    assert "rc=1" in result.stdout, result.stdout + result.stderr
    assert (sandbox["work"] / "out.pdf").read_text() != "split"

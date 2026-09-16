"""Behavioral test: Stage-1 CLIs run without optional OCR flags on bash 3.2.

macOS /bin/bash is 3.2, where "${arr[@]}" on an empty array counts as unbound
under `set -u` and aborts the script. pdf-combine built its OCR arguments from
such an array, so every run without --force-ocr or --split-columns died before
OCR: the default path of `reprocess-raw --output` and of the Obsidian
searchable-copy action. Bash 4.4 and later accept the expansion, so this test
detects the bug only where /bin/bash is 3.2 (macOS); elsewhere it still checks
that the default path reaches OCR.

External tools are stubs on PATH. The ocrmypdf stub fails, so each run ends at
the quality gate, after the argument handling under test.

Run with: python3 -m pytest bin/test -q
"""
import os
import subprocess
from pathlib import Path

import pytest


BIN = Path(__file__).resolve().parent.parent
BASH = "/bin/bash" if Path("/bin/bash").exists() else "bash"


def _stub(stub_dir, name, body):
    path = stub_dir / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("script", ["pdf-combine.sh", "pdf-workflow.sh"])
def test_cli_without_optional_flags_reaches_ocr(tmp_path, script):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "calls.log"
    # The Apple Vision plugin probe fails; every real OCR call is logged.
    _stub(stubs, "ocrmypdf", f'[ "$1" = "--plugin" ] && exit 1\necho "ocrmypdf $*" >> "{log}"\nexit 1\n')
    _stub(stubs, "qpdf", "echo 1\n")
    # fix_mediabox reads the page size; without it `read` ends the script under set -e.
    _stub(stubs, "pdfinfo", 'printf "Pages: 1\\nPage size: 595 x 842 pts (A4)\\n"\n')
    # gs_downscale runs gs unguarded: like the real one, write -sOutputFile.
    _stub(stubs, "gs", 'for a in "$@"; do case "$a" in -sOutputFile=*) out="${a#-sOutputFile=}" ;; esac; done\n'
                       'cp "${@: -1}" "$out"\n')
    for tool in ("pdftotext", "img2pdf"):
        _stub(stubs, tool, "exit 1\n")
    _stub(stubs, "python3", "exit 1\n")
    # `sysctl -n hw.memsize` exists only on macOS. 8 GB.
    _stub(stubs, "sysctl", "echo 8589934592\n")

    work = tmp_path / "work"
    work.mkdir()
    (work / "in.pdf").write_text("orig")
    env = dict(
        os.environ,
        PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
        VENV_ROOT=str(tmp_path / "no-venvs"),
    )

    result = subprocess.run(
        [BASH, str(BIN / script), str(work), "out"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    output = result.stdout + result.stderr

    assert "unbound variable" not in output, output
    calls = log.read_text().splitlines() if log.exists() else []
    assert any(call.startswith("ocrmypdf ") for call in calls), output
    assert not any("--force-ocr" in call for call in calls[:1]), calls

"""Behavioral test: the automatic split retry must not leak its split map.

Attempt 2 of ocr_with_retry splits the input, which points the global
SPLIT_MAP at "<input>_split_map.json". If that attempt fails, attempt 3 OCRs
the *unsplit* input — and quality_check's metric 1.5 verifies it against the
leftover map. column_tools.py verify then reports a page-count mismatch and
the engine switch fails the quality gate even though its OCR is fine.

External tools are replaced by stubs on PATH and PYTHON_BIN, so the test
needs neither ocrmypdf nor pikepdf.

Run with: python3 -m pytest bin/test -q
"""
import os
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.slow  # end-to-end runs; `make test-fast` skips them


BIN = Path(__file__).resolve().parent.parent


def _stub(stub_dir, name, body):
    path = stub_dir / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


def test_engine_switch_ignores_failed_split_retry_map(tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (work / "in.pdf").write_text("orig")
    log = tmp_path / "calls.log"

    # Only the engine-switch result ("good") has a text layer; attempts 1 and
    # 2 fail the gate's character-density metric.
    _stub(stubs, "pdftotext", '[ "$(cat "$2")" = good ] && head -c 300 /dev/zero | tr "\\0" x\nexit 0\n')
    _stub(stubs, "pdfinfo", 'echo "Pages: 1"\n')
    # column_tools.py: `split <in> <out> --map <map> <mode>` writes both files
    # like the real splitter; `verify` fails like verify_ocr_split does for an
    # unsplit PDF checked against a map with split pages.
    python_bin = _stub(stubs, "column-tools-python", f'''
echo "column_tools $2" >> "{log}"
case "$2" in
    split) echo split > "$4"; echo '{{"pages": []}}' > "$6"; exit 0 ;;
    verify) exit 1 ;;
esac
exit 1
''')

    script = f'''
set -euo pipefail
SCRIPT_DIR="{BIN}"
source "{BIN}/pdf-lib.sh"
USE_APPLE=false; SPLIT_COLUMNS=false; ENGINE_DESC="Tesseract"
PYTHON_BIN="{python_bin}"
WORK_DIR="$PWD"
ocr_args=(-l deu --deskew)
resolve_engine() {{ USE_APPLE=true; ENGINE_DESC="Apple Vision"; }}
# Only the engine switch rebuilds its args with --force-ocr.
run_ocr() {{
    local args="$3[*]"
    case " ${{!args}} " in
        *" --force-ocr "*) echo good > "$2" ;;
        *) echo bad > "$2" ;;
    esac
}}
rc=0; ocr_with_retry in.pdf out.pdf apple ocr_args || rc=$?
echo "rc=$rc map=[$SPLIT_MAP]"
'''
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=work, env=dict(os.environ, PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}"),
        capture_output=True, text=True, timeout=30,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    # The split retry really ran and left its map on disk.
    assert "column_tools split" in log.read_text().splitlines(), output
    assert (work / "in_split_map.json").exists()
    # The engine switch passes the gate without being verified against it.
    assert "rc=0 map=[]" in result.stdout, output
    assert "column_tools verify" not in log.read_text().splitlines(), output
    assert (work / "out.pdf").read_text() == "good\n"

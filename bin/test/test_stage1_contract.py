"""Stage-1 side of the CLI contract with the plugin (Issue #55).

Stage 1 has no structured channel: the plugin reads the B5 short pages and
the failure reason from human output. contracts/cli-contract.json holds the
lines the real scripts print, and plugin/test/cli-contract.test.ts parses the
same lines, so a reworded message breaks one of the two suites.

pdftotext and pikepdf are stubbed as in test_reprocess_raw_output.py.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent
CONTRACT = json.loads(
    (BIN.parent / "contracts" / "cli-contract.json").read_text(encoding="utf-8"))
STAGE1 = CONTRACT["stage1"]

FAKE_PIKEPDF = '''
class _Document:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.pages = [None] * f.read().count(b"\\f")


class Pdf:
    @staticmethod
    def open(path):
        return _Document(path)
'''


def test_verify_pages_prints_the_contract_short_page_lines(tmp_path):
    spec = STAGE1["shortPages"]
    (tmp_path / "pikepdf.py").write_text(FAKE_PIKEPDF)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    pdftotext = stubs / "pdftotext"
    pdftotext.write_text('#!/bin/bash\ncat "$2"\n')
    pdftotext.chmod(0o755)
    pdf = tmp_path / "casebook.pdf"
    pdf.write_text("".join(page + "\f" for page in spec["pages"]))
    env = dict(os.environ, PYTHONPATH=str(tmp_path),
               PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}")

    result = subprocess.run(
        [sys.executable, str(BIN / "column_tools.py"), "verify-pages", str(pdf),
         "--min-chars", str(spec["minChars"])],
        capture_output=True, text=True, env=env, timeout=30, check=False)

    assert result.returncode == 1, result.stderr
    assert result.stderr.splitlines() == spec["stderr"]


def test_reprocess_raw_reports_its_failure_on_a_contract_line(tmp_path):
    spec = STAGE1["failure"]

    result = subprocess.run(
        ["bash", str(BIN / "reprocess-raw.sh"), "missing.pdf"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False)

    assert result.returncode == CONTRACT["exitCodes"]["error"]
    assert result.stdout.splitlines() == spec["stdout"]

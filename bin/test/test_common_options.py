"""Behavioral tests for issue #48: common Stage-1 options parse the same way.

lib_init used to infer "not supplied" by comparing a value with its default,
so an explicit `--jobs 2` was replaced by RAM auto-detection and `--fast`
overrode an explicit `--dpi 300`. Unknown options only warned, and a missing
option value died with Bash's `unbound variable`. These tests pin the shared
parser in pdf-lib.sh for all three CLIs.

External tools are stubs on PATH. The ocrmypdf stub logs its call and fails,
so a valid run ends at the quality gate after the OCR arguments are built.

Run with: python3 -m pytest bin/test -q
"""
import os
import subprocess
from pathlib import Path

import pytest


BIN = Path(__file__).resolve().parent.parent
BASH = "/bin/bash" if Path("/bin/bash").exists() else "bash"

CLIS = [
    ("pdf-auto.sh", []),
    ("pdf-combine.sh", ["out"]),
    ("pdf-workflow.sh", ["out"]),
]
CLI_IDS = [script for script, _ in CLIS]

GB = 1073741824


def _stub(stub_dir, name, body):
    path = stub_dir / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


def _sandbox(tmp_path, ram_gb):
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
    # `sysctl -n hw.memsize` exists only on macOS.
    _stub(stubs, "sysctl", f"echo {ram_gb * GB}\n")

    work = tmp_path / "work"
    work.mkdir()
    (work / "in.pdf").write_text("orig")
    env = dict(
        os.environ,
        PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
        VENV_ROOT=str(tmp_path / "no-venvs"),
    )
    return {"log": log, "work": work, "env": env}


def _run(tmp_path, script, positional, options, ram_gb=64):
    box = _sandbox(tmp_path, ram_gb)
    result = subprocess.run(
        [BASH, str(BIN / script), str(box["work"]), *positional, *options],
        env=box["env"], capture_output=True, text=True, timeout=30,
    )
    calls = box["log"].read_text().splitlines() if box["log"].exists() else []
    return result, calls


def _settings(result):
    """The `Jobs: N | DPI: M` line lib_init prints, as (jobs, dpi)."""
    for line in result.stdout.splitlines():
        if "Jobs:" in line and "DPI:" in line:
            jobs, dpi = line.split("Jobs:")[1].split("| DPI:")
            return int(jobs), int(dpi)
    raise AssertionError(result.stdout + result.stderr)


# ── Explicit values ────────────────────────────────────────────────────────

@pytest.mark.parametrize("ram_gb", [8, 64])
@pytest.mark.parametrize("script,positional", CLIS, ids=CLI_IDS)
def test_explicit_default_jobs_is_not_auto_detected(tmp_path, script, positional, ram_gb):
    result, calls = _run(tmp_path, script, positional, ["--jobs", "2"], ram_gb)

    assert _settings(result)[0] == 2
    ocr_calls = [call for call in calls if call.startswith("ocrmypdf ")]
    assert ocr_calls, result.stdout + result.stderr
    assert all("--jobs 2 " in call for call in ocr_calls), ocr_calls


@pytest.mark.parametrize("script,positional", CLIS, ids=CLI_IDS)
def test_unset_jobs_is_auto_detected(tmp_path, script, positional):
    result, _ = _run(tmp_path, script, positional, [], ram_gb=64)

    assert _settings(result) == (4, 300)


def test_fast_fills_only_unset_values(tmp_path):
    result, _ = _run(tmp_path, "pdf-auto.sh", [], ["--fast"])

    assert _settings(result) == (1, 200)


@pytest.mark.parametrize("options", [
    ["--fast", "--dpi", "300", "--jobs", "2"],
    ["--dpi", "300", "--jobs", "2", "--fast"],
])
def test_explicit_values_take_precedence_over_fast(tmp_path, options):
    result, _ = _run(tmp_path, "pdf-auto.sh", [], options)

    assert _settings(result) == (2, 300)


# ── Fail fast ──────────────────────────────────────────────────────────────

def _assert_usage_error(result, calls, message):
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert message in result.stderr, output
    assert "unbound variable" not in output, output
    # Rejected before lib_init: no dependency check, no OCR.
    assert "Checking dependencies" not in output, output
    assert calls == [], calls


@pytest.mark.parametrize("option", ["--engine", "--dpi", "--jobs"])
@pytest.mark.parametrize("script,positional", CLIS, ids=CLI_IDS)
def test_missing_value_is_a_usage_error(tmp_path, script, positional, option):
    result, calls = _run(tmp_path, script, positional, [option])

    _assert_usage_error(result, calls, f"{option} needs a value")


@pytest.mark.parametrize("script,positional", CLIS, ids=CLI_IDS)
def test_option_is_not_taken_as_a_value(tmp_path, script, positional):
    result, calls = _run(tmp_path, script, positional, ["--jobs", "--split-columns"])

    _assert_usage_error(result, calls, "--jobs needs a value")


def test_missing_output_dir_is_a_usage_error(tmp_path):
    result, calls = _run(tmp_path, "pdf-auto.sh", [], ["--output-dir"])

    _assert_usage_error(result, calls, "--output-dir needs a value")


@pytest.mark.parametrize("script,positional", CLIS, ids=CLI_IDS)
def test_unknown_option_fails(tmp_path, script, positional):
    result, calls = _run(tmp_path, script, positional, ["--jobs", "2", "--bogus"])

    _assert_usage_error(result, calls, "Unknown option: --bogus")


@pytest.mark.parametrize("script,positional,option", [
    ("pdf-combine.sh", ["out"], "--fast"),
    ("pdf-combine.sh", ["out"], "--cleanup"),
    ("pdf-workflow.sh", ["out"], "--force-ocr"),
    ("pdf-auto.sh", [], "--force-ocr"),
])
def test_other_clis_specific_option_fails(tmp_path, script, positional, option):
    result, calls = _run(tmp_path, script, positional, [option])

    _assert_usage_error(result, calls, f"Unknown option: {option}")


@pytest.mark.parametrize("options,message", [
    (["--jobs", "0"], "--jobs must be a positive whole number"),
    (["--jobs", "two"], "--jobs must be a positive whole number"),
    (["--dpi", "-5"], "--dpi must be a whole number"),
    (["--dpi", "3.5"], "--dpi must be a whole number"),
    (["--engine", "paddle"], "--engine must be 'auto', 'apple', or 'tesseract'"),
])
@pytest.mark.parametrize("script,positional", CLIS, ids=CLI_IDS)
def test_invalid_value_is_a_usage_error(tmp_path, script, positional, options, message):
    result, calls = _run(tmp_path, script, positional, options)

    _assert_usage_error(result, calls, message)

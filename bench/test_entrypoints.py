"""Cheap import smoke tests for the supported benchmark entry points."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


BENCH = Path(__file__).resolve().parent
REPOSITORY = BENCH.parent
SUPPORTED_ENTRYPOINTS = (
    "build_bench.py",
    "bench_ocr.py",
    "regress_steg.py",
    "regress_randlabel.py",
    "regress_randmarke.py",
    "randlabel_debug.py",
)


@pytest.mark.parametrize("script_name", SUPPORTED_ENTRYPOINTS)
def test_supported_entrypoint_imports(script_name):
    script = BENCH / script_name
    code = (
        "import runpy, sys; "
        f"sys.path.insert(0, {str(BENCH)!r}); "
        f"runpy.run_path({str(script)!r}, run_name='bench_smoke')"
    )
    environment = os.environ.copy()
    environment["VAULT_ROOT"] = str(REPOSITORY)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPOSITORY,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

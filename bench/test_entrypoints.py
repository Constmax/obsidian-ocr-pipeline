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
    "regress_footnote.py",
    "randlabel_debug.py",
    "reading_order.py",
    "structure_bench.py",
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


def test_page_helper_runs_against_a_real_page(tmp_path):
    """Importing is not enough — the regress scripts share seite_bauen().

    Issue #51 moved running_lines()/textlayer_lines() out of pdf2md.py and
    replaced assembly.set_running() with an AssemblyContext. The import smoke
    test above stayed green through that, because it never calls main(). This
    test does call the helper, so a moved or renamed symbol fails here.
    """
    fitz = pytest.importorskip("fitz")
    sys.path.insert(0, str(BENCH))
    try:
        # regress_steg imports paths, which puts pdf2md/ on sys.path.
        from regress_steg import seite_bauen
        import assembly
        import conversion
    finally:
        sys.path.remove(str(BENCH))

    pdf = tmp_path / "helper.pdf"
    with fitz.open() as document:
        for number in (1, 2):
            page = document.new_page(width=600, height=800)
            page.insert_text(fitz.Point(40, 60), "Repeated running head",
                             fontsize=9)
            page.insert_text(fitz.Point(40, 300),
                             f"Body text of page {number}.", fontsize=11)
        document.save(pdf)

    with fitz.open(pdf) as document:
        context = assembly.AssemblyContext(conversion.running_lines(document))
        paragraphs = seite_bauen(document[0], context)

    assert "Repeated running head" in context.running_lines
    assert isinstance(paragraphs, list)
    assert any("Body text of page 1." in text for text in paragraphs)
    assert not any("Repeated running head" in text for text in paragraphs)

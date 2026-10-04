"""Behavioral tests for issue #201: `--dpi` is an exact upper limit.

`gs_downscale` (bin/pdf-lib.sh) runs the real Ghostscript on a synthetic A4
page holding one JPEG scan. A scan above the limit comes out at the limit, as
JPEG; a scan at the limit passes through without a re-encode.

Run with: python3 -m pytest bin/test -q
"""
import io
import shutil
import subprocess
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")
Image = pytest.importorskip("PIL.Image")

pytestmark = [
    pytest.mark.slow,  # runs the real Ghostscript; `make test-fast` skips it
    pytest.mark.skipif(shutil.which("gs") is None, reason="needs Ghostscript"),
]

LIB = Path(__file__).resolve().parent.parent / "pdf-lib.sh"
A4_IN = (595 / 72, 842 / 72)


def _scan_pdf(path, dpi):
    """A4 PDF with one RGB JPEG of `dpi` resolution; returns the JPEG bytes."""
    size = (round(A4_IN[0] * dpi), round(A4_IN[1] * dpi))
    # Paper grain: Ghostscript's AutoFilter keeps flat synthetic images
    # lossless (Flate); a real scan is continuous tone and gets JPEG.
    image = Image.effect_noise(size, 40).convert("RGB")
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=90, dpi=(dpi, dpi))
    jpeg = buf.getvalue()
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=jpeg)
    doc.save(path)
    return jpeg


def _downscale(src, out, dpi):
    subprocess.run(["bash", "-c", f'source "{LIB}"; gs_downscale "$1" "$2" "$3"', "_",
                    str(src), str(out), str(dpi)], check=True, capture_output=True)


def _only_image(path):
    doc = fitz.open(path)
    (xref, *_), = doc[0].get_images()
    return doc.extract_image(xref)


def test_scan_above_limit_comes_out_at_limit_as_jpeg(tmp_path):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _scan_pdf(src, 400)  # below Ghostscript's default 1.5x threshold (450)

    _downscale(src, out, 300)

    image = _only_image(out)
    assert image["ext"] == "jpeg"
    assert round(image["width"] / A4_IN[0]) == 300


def test_scan_at_limit_passes_through_unchanged(tmp_path):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    jpeg = _scan_pdf(src, 300)

    _downscale(src, out, 300)

    # Pixels, not bytes: Ghostscript 10.02 (CI) appends a second EOI marker
    # to a passed-through JPEG. A re-encode would change the pixels.
    image = _only_image(out)
    assert image["ext"] == "jpeg"
    assert _pixels(image["image"]) == _pixels(jpeg)


def _pixels(jpeg):
    return Image.open(io.BytesIO(jpeg)).tobytes()

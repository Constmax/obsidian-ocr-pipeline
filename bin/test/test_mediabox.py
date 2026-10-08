"""Behavioral tests for issue #219: `fix_mediabox` refits only pixel-sized pages.

`fix_mediabox` (bin/pdf-lib.sh) runs the real pdfinfo, Ghostscript and qpdf
on synthetic PDFs. A page counts as pixel-sized when its longer side is above
1263 pt (1.5 × A4 height); only such pages become A4, the others keep their
size, and the page order stays.

Run with: python3 -m pytest bin/test -q
"""
import shutil
import subprocess
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

pytestmark = [
    pytest.mark.slow,  # runs the real Ghostscript; `make test-fast` skips it
    pytest.mark.skipif(any(shutil.which(t) is None for t in ("gs", "qpdf", "pdfinfo")),
                       reason="needs Ghostscript, qpdf and pdfinfo"),
]

LIB = Path(__file__).resolve().parent.parent / "pdf-lib.sh"
A4 = (595, 842)
PIXEL = (2439, 3413)
SLIDE = (960, 540)
LANDSCAPE_A4 = (842, 595)


def _upright(size):
    return (*size, 0)


def _pdf(path, sizes):
    """One page per size, each marked with its 1-based number as text."""
    doc = fitz.open()
    for number, (w, h) in enumerate(sizes, 1):
        doc.new_page(width=w, height=h).insert_text((20, 40), f"page{number}", fontsize=24)
    doc.save(path)


def _fix(tmp_path, sizes):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _pdf(src, sizes)
    subprocess.run(["bash", "-c", f'source "{LIB}"; fix_mediabox "$1" "$2"', "_",
                    str(src), str(out)], check=True, capture_output=True)
    doc = fitz.open(out)
    # The MediaBox and /Rotate as written: Ghostscript's fit turns a landscape
    # page onto portrait A4 and adds /Rotate, which page.rect would hide.
    return [(round(p.mediabox.width), round(p.mediabox.height), p.rotation) for p in doc], \
           [p.get_text().strip() for p in doc]


@pytest.mark.parametrize("size", [SLIDE, LANDSCAPE_A4, A4])
def test_page_that_is_not_pixel_sized_keeps_its_size(tmp_path, size):
    sizes, _ = _fix(tmp_path, [size])

    assert sizes == [_upright(size)]


def test_pixel_sized_page_becomes_a4(tmp_path):
    sizes, texts = _fix(tmp_path, [PIXEL, PIXEL])

    assert sizes == [_upright(A4)] * 2
    assert texts == ["page1", "page2"]


def test_mixed_document_refits_only_the_pixel_sized_pages(tmp_path):
    sizes, texts = _fix(tmp_path, [SLIDE, PIXEL, LANDSCAPE_A4, PIXEL, A4])

    assert sizes == [_upright(s) for s in (SLIDE, A4, LANDSCAPE_A4, A4, A4)]
    assert texts == ["page1", "page2", "page3", "page4", "page5"]

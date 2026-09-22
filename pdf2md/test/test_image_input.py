"""Image input normalized into a one-page PDF at the boundary (Issue #100)."""

import subprocess
import sys
from pathlib import Path

import fitz
import pytest

from conversion import (INPUT_SUFFIXES, ConversionRequest, UnsupportedInput,
                        analyze_pages, convert_document, ensure_supported_input,
                        open_document, page_image_dpi)

REPOSITORY = Path(__file__).resolve().parent.parent.parent
PDF2MD_PY = REPOSITORY / "pdf2md" / "pdf2md.py"


def _page_pixmap(dpi=300, text="Seite aus einem Scan"):
    """Render one A4 page at `dpi`."""
    with fitz.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_text(fitz.Point(60, 200), text, fontsize=28)
        return page.get_pixmap(dpi=dpi)


def _write_page_image(path: Path, dpi=300, **save):
    """Write one A4 page to an image file carrying `dpi` as its resolution."""
    _page_pixmap(dpi).pil_save(path, dpi=(dpi, dpi), **save)
    return path


@pytest.mark.parametrize("suffix", sorted(INPUT_SUFFIXES - {".pdf"}))
def test_every_accepted_image_suffix_opens_as_a_single_pdf_page(tmp_path, suffix):
    source = _write_page_image(tmp_path / f"scan{suffix}")

    with open_document(source) as document:
        assert document.page_count == 1
        page = document[0]
        assert page.get_text("text").strip() == ""
        assert len(page.get_images(full=True)) == 1


def test_pdf_input_passes_through_unchanged(tmp_path):
    pdf = tmp_path / "input.pdf"
    with fitz.open() as document:
        document.new_page()
        document.new_page()
        document.save(pdf)

    with open_document(pdf) as document:
        assert document.page_count == 2
        assert document.is_pdf


@pytest.mark.parametrize("name", ["scan.webp", "scan.heic", "notes.txt", "scan"])
def test_unsupported_suffixes_are_rejected_by_name(name):
    with pytest.raises(UnsupportedInput) as error:
        ensure_supported_input(Path(name))

    assert "unsupported input format" in str(error.value)
    assert ".pdf" in str(error.value)


def test_unsupported_suffix_exits_with_a_message_not_a_traceback(tmp_path):
    source = tmp_path / "scan.webp"
    source.write_bytes(b"not an image")

    result = subprocess.run(
        [sys.executable, str(PDF2MD_PY), str(source), "--out", str(tmp_path / "out")],
        cwd=str(REPOSITORY), capture_output=True, text=True, timeout=60)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "unsupported input format .webp" in result.stderr


def test_image_input_lands_on_the_ocr_branch_as_one_page(tmp_path):
    source = _write_page_image(tmp_path / "scan.png")
    request = ConversionRequest(pdf=source, output_dir=tmp_path / "out",
                                temp_root=tmp_path / "scratch",
                                no_dictionary=True)
    (tmp_path / "scratch").mkdir()

    pages, _context = analyze_pages(request, tmp_path / "scratch")

    assert len(pages) == 1
    assert pages[0].needs_ocr is True
    assert pages[0].textlayer_lines is None
    assert pages[0].text_characters == 0


def test_the_page_image_keeps_the_source_resolution(tmp_path):
    """A 300 dpi scan must not be resampled down to --dpi (150)."""
    source = _write_page_image(tmp_path / "scan.png", dpi=300)
    request = ConversionRequest(pdf=source, output_dir=tmp_path / "out",
                                dpi=150, temp_root=tmp_path / "scratch",
                                no_dictionary=True)
    (tmp_path / "scratch").mkdir()

    pages, _context = analyze_pages(request, tmp_path / "scratch")
    page = pages[0]

    assert page.image_path.suffix == ".png"
    assert page.image_path.parent == tmp_path / "scratch"
    assert page.image_path.read_bytes() == source.read_bytes()
    assert page.image_dpi == pytest.approx(300, rel=0.01)
    # 300 dpi on A4, not the 150 dpi a re-render would have produced.
    assert fitz.Pixmap(page.image_path).width == _page_pixmap(300).width


def test_an_image_without_resolution_metadata_is_laid_out_on_a_page(tmp_path):
    """A JPEG's decorative dpi must not turn an A4 scan into a 49-inch page."""
    pixmap = _page_pixmap(300)
    source = tmp_path / "scan.jpg"
    pixmap.pil_save(source, format="JPEG", quality=80)  # no dpi metadata

    with open_document(source) as document:
        page = document[0]
        assert max(page.rect.width, page.rect.height) == pytest.approx(842, abs=1)
        embedded = document.extract_image(page.get_images(full=True)[0][0])
        assert embedded["width"] == pixmap.width
        # A4 with every pixel kept reads back as the scan's real resolution,
        # not as the 96 dpi fitz assumes for an image that declares none.
        assert page_image_dpi(document, page) == pytest.approx(300, rel=0.02)


def test_a_png_converts_to_markdown_through_the_model(tmp_path):
    source = _write_page_image(tmp_path / "scan.png")
    seen = []

    def fake_ocr(image, max_tokens=None):
        seen.append(Path(image))
        return "Seite aus einem Scan"

    result = convert_document(
        ConversionRequest(pdf=source, output_dir=tmp_path / "out",
                          temp_root=tmp_path / "scratch", no_dictionary=True),
        fake_ocr,
    )

    assert result.completed is True
    assert result.pages_ocr == 1
    assert result.pages_textlayer == 0
    assert result.target == tmp_path / "out" / "scan.md"
    assert "Seite aus einem Scan" in result.markdown
    assert seen and seen[0].suffix == ".png"


def test_a_jpg_converts_to_markdown_through_the_model(tmp_path):
    source = _write_page_image(tmp_path / "scan.jpg", format="JPEG", quality=85)

    result = convert_document(
        ConversionRequest(pdf=source, output_dir=tmp_path / "out",
                          temp_root=tmp_path / "scratch", no_dictionary=True),
        lambda image, max_tokens=None: "Seite aus einem Scan",
    )

    assert result.completed is True
    assert result.pages_ocr == 1
    assert result.target == tmp_path / "out" / "scan.md"
    assert "Seite aus einem Scan" in result.markdown


def _two_column_pixmap(dpi=200):
    """Render a page with a clear gutter, so `detect_layout` splits it."""
    with fitz.open() as document:
        page = document.new_page(width=595, height=842)
        for column in (60, 320):
            for line in range(30):
                page.insert_text(fitz.Point(column, 80 + line * 22),
                                 "Lorem ipsum dolor sit amet", fontsize=11)
        return page.get_pixmap(dpi=dpi)


def test_tiles_of_a_two_column_image_stay_beside_the_temporary_copy(tmp_path):
    """`tile_vertically()` writes beside the page image — never beside the input."""
    source = tmp_path / "scan.jpg"
    _two_column_pixmap().pil_save(source, format="JPEG", quality=90,
                                  dpi=(200, 200))
    scratch = tmp_path / "scratch"
    seen = []

    result = convert_document(
        # retries=0: the stub answers a dense page with four words, which the
        # ink check rightly calls derailed — the retiling is not what is
        # under test here.
        ConversionRequest(pdf=source, output_dir=tmp_path / "out",
                          temp_root=scratch, no_dictionary=True, retries=0),
        lambda image, max_tokens=None: seen.append(Path(image)) or "Lorem ipsum",
    )

    assert result.completed is True
    assert {path.name for path in seen} == {"_seite001_L.png", "_seite001_R.png"}
    assert {path.parent.parent for path in seen} == {scratch}
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [
        "out", "scan.jpg", "scratch"]

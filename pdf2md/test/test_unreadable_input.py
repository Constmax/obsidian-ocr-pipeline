"""Unreadable input ends in the contract's one-line error, not a traceback (Issue #220).

`docs/cli-contract.md` §2: exit 1 with one message line on stderr, which the
plugin shows as the failure reason; §4 the same for `pdf2md case`.
"""

import subprocess
import sys
from pathlib import Path

import fitz
import pytest

import cases
import pdf2md
from conversion import UnsupportedInput, open_document


REPOSITORY = Path(__file__).resolve().parent.parent.parent
PDF2MD_PY = REPOSITORY / "pdf2md" / "pdf2md.py"

# A well-formed PDF whose page tree is empty; fitz refuses to save one.
NO_PAGES_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
                b"trailer<</Root 1 0 R>>\n%%EOF\n")


def _encrypted_pdf(path: Path, user_pw="user") -> Path:
    with fitz.open() as document:
        document.new_page().insert_text((50, 50), "Lorem ipsum")
        document.save(path, encryption=fitz.PDF_ENCRYPT_AES_256,
                      user_pw=user_pw, owner_pw="owner")
    return path


def _run(source: Path, out: Path):
    return subprocess.run(
        [sys.executable, str(PDF2MD_PY), str(source), "--out", str(out)],
        cwd=str(REPOSITORY), capture_output=True, text=True, timeout=60)


def _last_line(text: str) -> str:
    return [line for line in text.splitlines() if line.strip()][-1]


def test_an_encrypted_pdf_is_unsupported_input(tmp_path):
    source = _encrypted_pdf(tmp_path / "locked.pdf")

    with pytest.raises(UnsupportedInput) as error, open_document(source):
        pass

    assert "password" in str(error.value)
    assert "locked.pdf" in str(error.value)


def test_a_pdf_with_only_an_owner_password_still_opens(tmp_path):
    """Publishers encrypt against editing; such a PDF opens without a password."""
    source = _encrypted_pdf(tmp_path / "protected.pdf", user_pw="")

    with open_document(source) as document:
        assert "Lorem ipsum" in document[0].get_text()


def test_a_pdf_without_pages_is_unsupported_input(tmp_path):
    source = tmp_path / "hollow.pdf"
    source.write_bytes(NO_PAGES_PDF)

    with pytest.raises(UnsupportedInput) as error, open_document(source):
        pass

    assert "no pages" in str(error.value)


@pytest.mark.slow
def test_an_encrypted_pdf_exits_before_the_out_folder_exists(tmp_path):
    out = tmp_path / "out"

    result = _run(_encrypted_pdf(tmp_path / "locked.pdf"), out)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "password" in _last_line(result.stderr)
    assert not out.exists()


@pytest.mark.slow
@pytest.mark.parametrize("content", [b"%PDF-1.4 garbage" * 10, b""],
                         ids=["corrupt", "empty"])
def test_an_unreadable_pdf_exits_with_one_line(tmp_path, content):
    source = tmp_path / "broken.pdf"
    source.write_bytes(content)

    result = _run(source, tmp_path / "out")

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert _last_line(result.stderr).startswith("pdf2md: ")
    assert "broken.pdf" in _last_line(result.stderr)


@pytest.mark.slow
def test_a_read_only_out_folder_exits_with_one_line(tmp_path, make_vector_pdf):
    source = tmp_path / "input.pdf"
    make_vector_pdf(source, pages=1)
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        result = _run(source, locked / "out")
    finally:
        locked.chmod(0o755)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert _last_line(result.stderr).startswith("pdf2md: ")
    assert "Permission denied" in _last_line(result.stderr)


@pytest.mark.parametrize("error", [
    OSError(28, "No space left on device"),     # write_page mid-run
    RuntimeError("model could not be loaded"),  # the model load
])
def test_a_failure_during_the_conversion_exits_with_one_line(
        tmp_path, monkeypatch, capsys, make_vector_pdf, error):
    source = tmp_path / "input.pdf"
    make_vector_pdf(source, pages=1)

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(pdf2md, "convert_document", fail)
    monkeypatch.setattr(sys, "argv", ["pdf2md", str(source), "--out", str(tmp_path / "out")])

    with pytest.raises(SystemExit) as exit_:
        pdf2md.main()

    assert exit_.value.code == f"pdf2md: {error}"  # sys.exit(str): exit 1, stderr


@pytest.mark.parametrize("error", [PermissionError(13, "Permission denied"),
                                   fitz.FileDataError("Failed to open file")])
def test_a_failing_case_command_exits_with_one_line(tmp_path, monkeypatch, capsys, error):
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(cases, "list_cases", fail)

    assert cases.main(["list", str(tmp_path / "preview.md")]) == 1
    assert capsys.readouterr().err == f"pdf2md case: {error}\n"

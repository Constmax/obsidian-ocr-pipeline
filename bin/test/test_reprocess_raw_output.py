"""Behavioral tests for `reprocess-raw --output` (issue #63).

The output mode must never modify the source, never overwrite a destination
(including one created while OCR runs), publish atomically, and leave no
partial PDF, hidden temporary file or `_FAILED_` artifact after failure or
cancellation. The B5 gate keeps its 50-character floor and exact
`--allow-pages` exemptions.

pdf-combine, pdfinfo and pdftotext are stubs; the real column_tools.py runs
the B5 gate against a fake pikepdf, so the tests need neither ocrmypdf nor
pikepdf. A fake PDF is plain text with a form feed after each page, which is
how `pdftotext -raw` separates pages.

Run with: python3 -m pytest bin/test -q
"""
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.slow  # end-to-end runs; `make test-fast` skips them


BIN = Path(__file__).resolve().parent.parent
LONG = "x" * 60


def _write_exe(path, body):
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


def _fake_pdf(pages):
    return "".join(page + "\f" for page in pages)


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

# pdf-combine <work-dir> <output-name> [options], steered by FAKE_* variables.
FAKE_COMBINE = '''
echo "$*" >> "$FAKE_LOG"
if [ -n "${FAKE_CREATE_FILE:-}" ]; then echo intruder > "$FAKE_CREATE_FILE"; fi
if [ -n "${FAKE_CREATE_DIR:-}" ]; then mkdir "$FAKE_CREATE_DIR"; fi
if [ -n "${FAKE_BLOCK:-}" ]; then touch "$FAKE_BLOCK"; sleep 60; fi
[ "${FAKE_COMBINE_EXIT:-0}" = 0 ] || exit "$FAKE_COMBINE_EXIT"
cp "$FAKE_RESULT" "$1/$2.pdf"
'''


@pytest.fixture
def sb(tmp_path):
    """A vault with casebook.pdf, a stubbed toolchain and a private TMPDIR."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shutil.copy(BIN / "reprocess-raw.sh", bin_dir)
    shutil.copy(BIN / "column_tools.py", bin_dir)
    shutil.copy(BIN / "pdf-lib.sh", bin_dir)
    _write_exe(bin_dir / "pdf-combine.sh", FAKE_COMBINE)

    # Tests put per-test stubs (ln, python3) here, ahead of everything else.
    override = tmp_path / "override"
    override.mkdir()
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _write_exe(stubs / "pdfinfo", 'printf "Pages: %s\\n" "$(tr -cd "\\f" < "$1" | wc -c | tr -d " ")"\n')
    _write_exe(stubs / "pdftotext", 'cat "$2"\n')

    fakes = tmp_path / "fakes" / "pikepdf"
    fakes.mkdir(parents=True)
    (fakes / "__init__.py").write_text(FAKE_PIKEPDF)
    venv_python = tmp_path / "venvs" / "ocrmypdf" / "bin" / "python3"
    venv_python.parent.mkdir(parents=True)
    _write_exe(venv_python, f'PYTHONPATH="{fakes.parent}" exec "{sys.executable}" "$@"\n')

    vault = tmp_path / "vault"
    vault.mkdir()
    source = vault / "casebook.pdf"
    source.write_text(_fake_pdf([LONG, LONG, LONG]))
    result = tmp_path / "result.pdf"
    result.write_text(_fake_pdf(["y" * 60] * 3))
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    (tmp_path / "home").mkdir()

    env = dict(
        os.environ,
        PATH=os.pathsep.join([str(override), str(stubs), os.environ["PATH"]]),
        HOME=str(tmp_path / "home"),
        VENV_ROOT=str(tmp_path / "venvs"),
        TMPDIR=str(tmp),
        FAKE_LOG=str(tmp_path / "combine.log"),
        FAKE_RESULT=str(result),
    )
    return SimpleNamespace(
        root=tmp_path, script=bin_dir / "reprocess-raw.sh", override=override,
        vault=vault, source=source, source_bytes=source.read_bytes(),
        source_mtime=source.stat().st_mtime_ns, result=result, tmp=tmp,
        log=tmp_path / "combine.log", dest=vault / "casebook-ocr.pdf", env=env,
    )


def _command(sb, args):
    return ["bash", str(sb.script), str(sb.source), *map(str, args)]


def _run(sb, *args, **env):
    result = subprocess.run(
        _command(sb, args), env=dict(sb.env, **env),
        capture_output=True, text=True, timeout=30,
    )
    result.output = result.stdout + result.stderr
    return result


def _assert_clean(sb, names):
    """Only `names` in the vault, no temporary directory left, source untouched."""
    assert sorted(p.name for p in sb.vault.iterdir()) == sorted(names)
    assert list(sb.tmp.iterdir()) == []
    assert sb.source.read_bytes() == sb.source_bytes
    assert sb.source.stat().st_mtime_ns == sb.source_mtime


# ── Success ────────────────────────────────────────────────────────────────

def test_output_publishes_result_and_keeps_source(sb):
    result = _run(sb, "--output", sb.dest, "--engine", "tesseract")

    assert result.returncode == 0, result.output
    assert sb.dest.read_text() == sb.result.read_text()
    _assert_clean(sb, ["casebook.pdf", "casebook-ocr.pdf"])
    calls = sb.log.read_text().splitlines()
    assert len(calls) == 1 and calls[0].endswith(" casebook_reprocessed --engine tesseract")
    # Existing text is preserved: --force-ocr is never added.
    assert "--force-ocr" not in calls[0]


def test_output_into_another_folder(sb):
    other = sb.root / "elsewhere"
    other.mkdir()
    result = _run(sb, "--output", other / "copy.pdf")

    assert result.returncode == 0, result.output
    assert [p.name for p in other.iterdir()] == ["copy.pdf"]
    _assert_clean(sb, ["casebook.pdf"])


# ── Rejection before processing ────────────────────────────────────────────

@pytest.mark.parametrize("spelling", ["same", "dotted", "symlinked-folder", "hard-link"])
def test_output_rejects_the_source_itself(sb, spelling):
    names = ["casebook.pdf"]
    if spelling == "same":
        dest = sb.source
    elif spelling == "dotted":
        dest = f"{sb.vault}/./../vault/casebook.pdf"
    elif spelling == "symlinked-folder":
        alias = sb.root / "alias"
        alias.symlink_to(sb.vault)
        dest = alias / "casebook.pdf"
    else:
        dest = sb.vault / "alias.pdf"
        os.link(sb.source, dest)
        names.append("alias.pdf")

    result = _run(sb, "--output", dest)

    assert result.returncode != 0
    assert "source file itself" in result.output, result.output
    assert not sb.log.exists()
    _assert_clean(sb, names)


@pytest.mark.parametrize("kind", ["file", "directory", "dangling-symlink"])
def test_output_rejects_existing_destination(sb, kind):
    if kind == "file":
        sb.dest.write_text("keep me")
    elif kind == "directory":
        sb.dest.mkdir()
    else:
        sb.dest.symlink_to(sb.vault / "missing.pdf")

    result = _run(sb, "--output", sb.dest)

    assert result.returncode != 0
    assert "already exists" in result.output, result.output
    assert not sb.log.exists()
    if kind == "file":
        assert sb.dest.read_text() == "keep me"
    _assert_clean(sb, ["casebook.pdf", "casebook-ocr.pdf"])


def test_output_without_pikepdf_fails_before_ocr(sb):
    _write_exe(sb.override / "python3", "exit 1\n")

    result = _run(sb, "--output", sb.dest, VENV_ROOT=str(sb.root / "no-venvs"))

    assert result.returncode != 0
    assert "pikepdf" in result.output, result.output
    assert not sb.log.exists()
    _assert_clean(sb, ["casebook.pdf"])


# ── Own-flag values fail fast ──────────────────────────────────────────────

@pytest.mark.parametrize("args,message", [
    (["--min-chars"], "--min-chars needs a value"),
    (["--allow-pages"], "--allow-pages needs a value"),
    (["--output"], "--output needs a value"),
    (["--min-chars", "--force-ocr"], "--min-chars needs a value"),
    (["--allow-pages", "--force-ocr"], "--allow-pages needs a value"),
    (["--output", "--force-ocr"], "--output needs a value"),
    (["--min-chars", "abc"], "--min-chars must be a whole number"),
    (["--min-chars", "-5"], "--min-chars must be a whole number"),
    (["--output", "a.pdf", "--output", "b.pdf"], "--output needs exactly one file name"),
])
def test_own_flag_values_fail_before_processing(sb, args, message):
    result = _run(sb, *args)

    assert result.returncode != 0, result.output
    assert message in result.output, result.output
    assert "unbound variable" not in result.output, result.output
    assert not sb.log.exists()
    _assert_clean(sb, ["casebook.pdf"])


# ── Publication without overwriting ────────────────────────────────────────

@pytest.mark.parametrize("kind", ["file", "directory"])
def test_output_created_during_run_is_not_overwritten(sb, kind):
    create = "FAKE_CREATE_FILE" if kind == "file" else "FAKE_CREATE_DIR"

    result = _run(sb, "--output", sb.dest, **{create: str(sb.dest)})

    assert result.returncode != 0
    assert "appeared during processing" in result.output, result.output
    if kind == "file":
        assert sb.dest.read_text() == "intruder\n"
    else:
        # BSD ln would have linked the result inside the new directory.
        assert list(sb.dest.iterdir()) == []
    _assert_clean(sb, ["casebook.pdf", "casebook-ocr.pdf"])


def test_output_link_failure_writes_nothing(sb):
    _write_exe(sb.override / "ln", 'echo "ln: Operation not supported" >&2\nexit 1\n')

    result = _run(sb, "--output", sb.dest)

    assert result.returncode != 0
    assert "hard links" in result.output, result.output
    _assert_clean(sb, ["casebook.pdf"])


# ── Failure and cancellation ───────────────────────────────────────────────

@pytest.mark.parametrize("failure", ["combine-fails", "empty-output", "page-count", "short-page"])
def test_output_failure_leaves_nothing(sb, failure):
    env = {}
    if failure == "combine-fails":
        env["FAKE_COMBINE_EXIT"] = "1"
    elif failure == "empty-output":
        sb.result.write_text("")
    elif failure == "page-count":
        sb.result.write_text(_fake_pdf([LONG] * 4))
    else:
        sb.result.write_text(_fake_pdf([LONG, "short", LONG]))

    result = _run(sb, "--output", sb.dest, **env)

    assert result.returncode != 0
    assert "No file written" in result.output or "remains unchanged" in result.output
    # No destination, no hidden temporary copy, no _FAILED_ artifact.
    _assert_clean(sb, ["casebook.pdf"])


@pytest.mark.parametrize("phase", ["ocr", "publish"])
def test_output_cancellation_leaves_nothing(sb, phase):
    blocked = sb.root / "blocked"
    env = dict(sb.env)
    if phase == "ocr":
        env["FAKE_BLOCK"] = str(blocked)
    else:
        # Block inside publication: the hidden copy exists, the link does not.
        _write_exe(sb.override / "ln", f'touch "{blocked}"\nsleep 60\n')

    # Cancellation targets the process group, like the plugin will (#64).
    proc = subprocess.Popen(
        _command(sb, ["--output", sb.dest]), env=env, start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while not blocked.exists():
            assert proc.poll() is None, proc.communicate()[0]
            assert time.monotonic() < deadline, "script never reached the blocking step"
            time.sleep(0.05)
        if phase == "publish":
            hidden = [p.name for p in sb.vault.iterdir() if p.name.startswith(".")]
            assert len(hidden) == 1 and hidden[0].startswith(".casebook-ocr.pdf.")
        os.killpg(proc.pid, signal.SIGTERM)
        output = proc.communicate(timeout=20)[0]
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

    assert proc.returncode != 0, output
    _assert_clean(sb, ["casebook.pdf"])


# ── B5 gate ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("chars,passes", [(49, False), (50, True)])
def test_output_b5_floor_is_50_characters_per_page(sb, chars, passes):
    sb.result.write_text(_fake_pdf([LONG, "y" * chars, LONG]))

    result = _run(sb, "--output", sb.dest)

    if passes:
        assert result.returncode == 0, result.output
        _assert_clean(sb, ["casebook.pdf", "casebook-ocr.pdf"])
    else:
        assert result.returncode != 0
        assert f"Page 2: only {chars} characters (min: 50)" in result.output, result.output
        _assert_clean(sb, ["casebook.pdf"])


def test_output_allow_pages_exempts_only_listed_pages(sb):
    sb.result.write_text(_fake_pdf(["", LONG, "cover"]))

    partial = _run(sb, "--output", sb.dest, "--allow-pages", "1")

    assert partial.returncode != 0
    assert "Page 3: only 5 characters" in partial.output, partial.output
    assert "Page 1:" not in partial.output
    _assert_clean(sb, ["casebook.pdf"])

    full = _run(sb, "--output", sb.dest, "--allow-pages", "1,3")

    assert full.returncode == 0, full.output
    # Exemptions change neither page numbering nor order.
    assert sb.dest.read_text() == sb.result.read_text()
    _assert_clean(sb, ["casebook.pdf", "casebook-ocr.pdf"])


# ── Legacy in-place mode ───────────────────────────────────────────────────

def test_in_place_mode_is_unchanged(sb):
    failing = _fake_pdf([LONG, "short", LONG])
    sb.result.write_text(failing)

    rejected = _run(sb)

    assert rejected.returncode != 0
    assert (sb.vault / "casebook_FAILED_pages.pdf").read_text() == failing
    assert sb.source.read_bytes() == sb.source_bytes

    sb.result.write_text(_fake_pdf(["y" * 60] * 3))
    accepted = _run(sb)

    assert accepted.returncode == 0, accepted.output
    assert sb.source.read_text() == sb.result.read_text()

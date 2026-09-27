"""Characterization tests for issue #50: one merge-to-OCR pipeline.

pdf-auto, pdf-combine and pdf-workflow share one sequence (merge, MediaBox
fix, downscale, optional split, OCR with quality gate, re-merge, publish).
These tests pin what each CLI hands off — which inputs reach OCR in which
order, which OCR flags it builds, what lands at the output path — and that
every failure leaves no output and no scratch files behind.

Every tool is a stub on PATH. Files are text; each stub wraps its input's
content, so the output shows the whole chain, e.g. `merged(ocr(split(a)))`.
The pikepdf Python (for column_tools.py) is a stub venv under VENV_ROOT.

Run with: python3 -m pytest bin/test -q
"""
import os
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.slow  # end-to-end runs; `make test-fast` skips them


BIN = Path(__file__).resolve().parent.parent
BASH = "/bin/bash" if Path("/bin/bash").exists() else "bash"
GB = 1073741824


def _stub(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)


# ocrmypdf <args...> <in> <out>: the Apple Vision probe fails, so the engine
# is Tesseract. FAKE_OCR_FAIL makes every OCR call fail.
OCRMYPDF = '''
[ "$1" = "--plugin" ] && exit 1
echo "ocrmypdf $*" >> "$FAKE_LOG"
[ -n "${FAKE_OCR_FAIL:-}" ] && exit 1
in="${@: -2:1}"; out="${@: -1}"
printf 'ocr(%s)' "$(cat "$in")" > "$out"
'''

# qpdf --empty --pages a b -- out, or qpdf --show-npages f
QPDF = '''
if [ "$1" = "--show-npages" ]; then echo 1; exit 0; fi
echo "qpdf $*" >> "$FAKE_LOG"
shift 2
parts=()
while [ "$1" != "--" ]; do parts+=("$(cat "$1")"); shift; done
( IFS=+; printf '%s' "${parts[*]}" ) > "$2"
'''

# The quality gate reads pdftotext: 300 characters pass, nothing fails.
# FAKE_BAD_TEXT lists contents (space-separated) whose OCR text is empty.
PDFTOTEXT = '''
content="$(cat "$2")"
for bad in ${FAKE_BAD_TEXT:-}; do [ "$content" = "$bad" ] && exit 0; done
head -c 300 /dev/zero | tr "\\0" x
'''

# column_tools.py <cmd> ...: split/merge wrap the content; verify accepts
# only split results, like verify_ocr_split for an unsplit PDF vs. a map.
PIKEPDF_PYTHON = '''
[ "$1" = "-c" ] && exit 0
cmd="$2"
echo "column_tools $cmd $(basename "$3") ${*:4}" >> "$FAKE_LOG"
case "$cmd" in
    split) printf 'split(%s)' "$(cat "$3")" > "$4"; echo "$7" > "$6" ;;
    merge) [ -n "${FAKE_MERGE_FAIL:-}" ] && exit 1
           printf 'merged(%s)' "$(cat "$3")" > "$4" ;;
    verify) case "$(cat "$3")" in *split\\(*) exit 0 ;; *) exit 1 ;; esac ;;
esac
'''


@pytest.fixture
def box(tmp_path):
    stubs = tmp_path / "stubs"
    log = tmp_path / "calls.log"
    _stub(stubs / "ocrmypdf", OCRMYPDF)
    _stub(stubs / "qpdf", QPDF)
    _stub(stubs / "pdftotext", PDFTOTEXT)
    _stub(stubs / "pdfinfo", 'printf "Pages: 1\\nPage size: 595 x 842 pts (A4)\\n"\n')
    _stub(stubs / "gs", 'for a in "$@"; do case "$a" in -sOutputFile=*) out="${a#-sOutputFile=}" ;; esac; done\n'
                        'cp "${@: -1}" "$out"\n')
    _stub(stubs / "img2pdf", 'echo "img2pdf $*" >> "$FAKE_LOG"\n'
                             'n=(); while [ "$1" != "-o" ]; do n+=("$1"); shift; done\n'
                             '( IFS=,; printf "img(%s)" "${n[*]}" ) > "$2"\n')
    _stub(stubs / "unpaper", "exit 0\n")
    # Word statistics of the garbage heuristic: no python3 → defer to metric 1.
    _stub(stubs / "python3", "exit 1\n")
    _stub(stubs / "sysctl", f"echo {64 * GB}\n")
    _stub(tmp_path / "venvs" / "ocrmypdf" / "bin" / "python3", PIKEPDF_PYTHON)

    work = tmp_path / "work"
    work.mkdir()
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    env = dict(
        os.environ,
        PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
        VENV_ROOT=str(tmp_path / "venvs"),
        TMPDIR=str(tmp),
        FAKE_LOG=str(log),
    )

    class Box:
        pass

    b = Box()
    b.work, b.tmp, b.log, b.env = work, tmp, log, env

    def run(script, *args, **fake):
        env = dict(b.env, **{f"FAKE_{k.upper()}": v for k, v in fake.items()})
        return subprocess.run(
            [BASH, str(BIN / script), str(work), *args],
            env=env, capture_output=True, text=True, timeout=30,
        )

    def calls(prefix=""):
        lines = log.read_text().splitlines() if log.exists() else []
        return [line for line in lines if line.startswith(prefix)]

    def files(folder=None):
        folder = folder or work
        return {p.name: p.read_text() for p in folder.iterdir() if p.is_file()}

    b.run, b.calls, b.files = run, calls, files
    return b


def _write(folder, contents):
    for name, text in contents.items():
        (folder / name).write_text(text)


def _ocr_flags(call):
    """The ocrmypdf flags that differ between the CLIs and modes."""
    words = call.split()
    return {flag for flag in ("--skip-text", "--force-ocr", "--clean", "--rotate-pages", "--deskew")
            if flag in words}


def _assert_no_scratch(box):
    assert list(box.tmp.iterdir()) == [], list(box.tmp.iterdir())


# ── Single-output CLIs: pdf-combine, pdf-workflow ──────────────────────────

SINGLE = ["pdf-combine.sh", "pdf-workflow.sh"]


@pytest.mark.parametrize("script", SINGLE)
def test_single_output_merges_in_version_order(box, script):
    _write(box.work, {"b.pdf": "b", "a10.pdf": "a10", "a2.pdf": "a2"})

    result = box.run(script, "out")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files() == {"a2.pdf": "a2", "a10.pdf": "a10", "b.pdf": "b",
                           "out.pdf": "ocr(a2+a10+b)"}
    [merge] = box.calls("qpdf")
    assert merge.startswith("qpdf --empty --pages a2.pdf a10.pdf b.pdf -- "), merge
    _assert_no_scratch(box)


@pytest.mark.parametrize("script,flags", [
    ("pdf-combine.sh", {"--skip-text", "--rotate-pages", "--deskew"}),
    ("pdf-workflow.sh", {"--skip-text", "--clean", "--rotate-pages", "--deskew"}),
])
def test_single_output_ocr_flags(box, script, flags):
    _write(box.work, {"a.pdf": "a"})

    box.run(script, "out")

    assert [_ocr_flags(c) for c in box.calls("ocrmypdf")] == [flags]


def test_combine_force_ocr(box):
    _write(box.work, {"a.pdf": "a"})

    box.run("pdf-combine.sh", "out", "--force-ocr")

    assert [_ocr_flags(c) for c in box.calls("ocrmypdf")] == [{"--force-ocr", "--rotate-pages", "--deskew"}]


@pytest.mark.parametrize("script", SINGLE)
def test_single_output_skips_its_own_previous_output(box, script):
    _write(box.work, {"a.pdf": "a", "out.pdf": "old result"})

    result = box.run(script, "out.pdf")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files()["out.pdf"] == "ocr(a)"


def test_workflow_appends_images_after_pdfs(box):
    _write(box.work, {"b.pdf": "b", "scan2.png": "", "scan10.png": "", "a.pdf": "a"})

    result = box.run("pdf-workflow.sh", "out")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files()["out.pdf"] == "ocr(a+b+img(scan2.png,scan10.png))"
    _assert_no_scratch(box)


def test_workflow_images_only(box):
    _write(box.work, {"scan.png": ""})

    result = box.run("pdf-workflow.sh", "out")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files()["out.pdf"] == "ocr(img(scan.png))"
    assert box.calls("qpdf") == []


@pytest.mark.parametrize("script", SINGLE)
def test_single_output_split_and_re_merge(box, script):
    _write(box.work, {"a.pdf": "a"})

    result = box.run(script, "out", "--split-columns")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files()["out.pdf"] == "merged(ocr(split(a)))"
    [split] = box.calls("column_tools split")
    assert split.endswith("--auto"), split
    assert len(box.calls("column_tools merge")) == 1
    ocr = box.calls("ocrmypdf")
    assert len(ocr) == 1 and not {"--rotate-pages", "--deskew"} & _ocr_flags(ocr[0])
    _assert_no_scratch(box)


@pytest.mark.parametrize("script", SINGLE)
def test_single_output_split_all_pages(box, script):
    _write(box.work, {"a.pdf": "a"})

    box.run(script, "out", "--split-columns-all")

    assert box.calls("column_tools split")[0].endswith("--all")


@pytest.mark.parametrize("script", SINGLE)
def test_single_output_keep_split(box, script):
    _write(box.work, {"a.pdf": "a"})

    result = box.run(script, "out", "--split-columns", "--keep-split")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files()["out.pdf"] == "ocr(split(a))"
    assert box.calls("column_tools merge") == []


# ── Single-output failures: no file written, nothing left behind ───────────

@pytest.mark.parametrize("script", SINGLE)
@pytest.mark.parametrize("options,fake", [
    ([], {"bad_text": "ocr(a) ocr(split(a))"}),              # quality gate
    (["--no-quality-gate"], {"ocr_fail": "1"}),              # OCR itself
    (["--split-columns"], {"merge_fail": "1"}),              # re-merge
], ids=["quality-gate", "ocr", "re-merge"])
def test_single_output_failure_writes_nothing(box, script, options, fake):
    _write(box.work, {"a.pdf": "a"})

    result = box.run(script, "out", *options, **fake)

    assert result.returncode != 0, result.stdout
    assert box.files() == {"a.pdf": "a"}
    assert "❌" in result.stdout + result.stderr
    _assert_no_scratch(box)


@pytest.mark.parametrize("script", SINGLE)
def test_single_output_no_quality_gate_skips_the_gate(box, script):
    _write(box.work, {"a.pdf": "a"})

    result = box.run(script, "out", "--no-quality-gate", bad_text="ocr(a)")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files()["out.pdf"] == "ocr(a)"
    assert len(box.calls("ocrmypdf")) == 1


@pytest.mark.parametrize("script", SINGLE)
def test_quality_gate_split_retry_is_re_merged(box, script):
    _write(box.work, {"a.pdf": "a"})

    result = box.run(script, "out", bad_text="ocr(a)")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files()["out.pdf"] == "merged(ocr(split(a)))"


# ── pdf-auto: one output per group ─────────────────────────────────────────

def test_auto_groups_parts_and_singles(box):
    _write(box.work, {"Fall Teil 2.pdf": "f2", "Fall Teil 10.pdf": "f10", "Fall Teil 1.pdf": "f1", "Urteil.pdf": "u"})

    result = box.run("pdf-auto.sh")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files(box.work / "_processed") == {
        "Fall.pdf": "ocr(f1+f2+f10)",
        "Urteil.pdf": "ocr(u)",
    }
    assert [_ocr_flags(c) for c in box.calls("ocrmypdf")] == [
        {"--skip-text", "--clean", "--rotate-pages", "--deskew"}] * 2
    _assert_no_scratch(box)


def test_auto_output_dir_and_cleanup(box, tmp_path):
    _write(box.work, {"a.pdf": "a", "b.pdf": "b"})
    out = tmp_path / "elsewhere"

    result = box.run("pdf-auto.sh", "--output-dir", str(out), "--cleanup",
                     bad_text="ocr(b) ocr(split(b))")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files(out) == {"a.pdf": "ocr(a)"}
    # Only the successful group is archived; the failed one stays in place.
    assert box.files(box.work / "_archive") == {"a.pdf": "a"}
    assert box.files() == {"b.pdf": "b"}
    assert "Failed: 1" in result.stdout


def test_auto_split_keep_split_and_failure_per_group(box):
    _write(box.work, {"a.pdf": "a", "b.pdf": "b"})

    result = box.run("pdf-auto.sh", "--split-columns", "--keep-split")

    assert box.files(box.work / "_processed") == {"a.pdf": "ocr(split(a))", "b.pdf": "ocr(split(b))"}
    assert box.calls("column_tools merge") == []

    box.log.unlink()
    result = box.run("pdf-auto.sh", "--split-columns", merge_fail="1")

    assert "Failed: 2" in result.stdout, result.stdout
    assert box.files(box.work / "_processed") == {}
    _assert_no_scratch(box)


def test_auto_split_retry_map_does_not_leak_into_the_next_group(box):
    """A split retry that succeeded for one group left its map in WORK_DIR, so
    the quality gate of the next, unsplit group verified against it."""
    _write(box.work, {"a.pdf": "a", "b.pdf": "b"})

    result = box.run("pdf-auto.sh", bad_text="ocr(a)")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files(box.work / "_processed") == {
        "a.pdf": "merged(ocr(split(a)))",
        "b.pdf": "ocr(b)",
    }

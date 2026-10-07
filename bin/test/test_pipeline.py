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


# ocrmypdf <args...> <in> <out>. The engine follows --plugin: Apple Vision
# and PaddleOCR are "installed" only with FAKE_APPLE / FAKE_PADDLE, otherwise
# their probe fails and Tesseract is left. An installed PaddleOCR answers
# `--paddle-check MODE` with "not ready" and FAKE_PADDLE_UNREADY as reason
# when that is set, or like a plugin from before that option (argparse
# usage error) with FAKE_PADDLE_OLD. Output: `ocr(...)` for Tesseract, `apple(...)`,
# `paddle(...)`. FAKE_OCR_FAIL lists engines whose OCR call fails; `1` fails
# every engine. A `--plugin` path that is no file fails like ocrmypdf.
OCRMYPDF = '''
engine=tesseract
case " $* " in
    *" ocrmypdf_appleocr "*) engine=apple ;;
    *" ocrmypdf_paddle "*) engine=paddle ;;
esac
if [ "${@: -1}" = "--help" ]; then
    case "$engine" in
        apple) [ -n "${FAKE_APPLE:-}" ] ;;
        paddle) [ -n "${FAKE_PADDLE:-}" ] ;;
    esac
    exit
fi
if [ "${@: -2:1}" = "--paddle-check" ]; then
    echo "ocrmypdf $*" >> "$FAKE_LOG"
    if [ -n "${FAKE_PADDLE_OLD:-}" ]; then
        echo "OCRmyPDF: error: the following arguments are required: output_pdf" >&2
        exit 2
    fi
    if [ -n "${FAKE_PADDLE_UNREADY:-}" ]; then
        printf 'PaddleOCR engine is not ready:\n  %s\n' "$FAKE_PADDLE_UNREADY" >&2
        exit 1
    fi
    echo "PaddleOCR engine is ready (${@: -1} mode)." >&2
    exit
fi
echo "ocrmypdf $*" >> "$FAKE_LOG"
prev=
for arg in "$@"; do
    if [ "$prev" = --plugin ]; then
        case "$arg" in */*) [ -f "$arg" ] || { echo "no plugin file $arg" >&2; exit 2; } ;; esac
    fi
    prev="$arg"
done
for failing in ${FAKE_OCR_FAIL:-}; do
    [ "$failing" = 1 ] || [ "$failing" = "$engine" ] && exit 1
done
in="${@: -2:1}"; out="${@: -1}"
[ "$engine" = tesseract ] && engine=ocr
printf '%s(%s)' "$engine" "$(cat "$in")" > "$out"
'''

# qpdf --empty --pages a b -- out, or qpdf --show-npages f
QPDF = '''
if [ "$1" = "--show-npages" ]; then echo 1; exit "${FAKE_NPAGES_RC:-0}"; fi
echo "qpdf $*" >> "$FAKE_LOG"
shift 2
parts=()
while [ "$1" != "--" ]; do parts+=("$(cat "$1")"); shift; done
( IFS=+; printf '%s' "${parts[*]}" ) > "$2"
'''

# The quality gate reads pdftotext: 300 characters pass, nothing fails.
# FAKE_BAD_TEXT lists contents (space-separated) whose OCR text is empty.
# FAKE_TEXT_CHARS sets the count of the others.
# Like poppler, every page ends in a form feed, an empty one too.
PDFTOTEXT = '''
content="$(cat "$2")"
for bad in ${FAKE_BAD_TEXT:-}; do [ "$content" = "$bad" ] && { printf '\\f'; exit 0; }; done
head -c "${FAKE_TEXT_CHARS:-300}" /dev/zero | tr "\\0" x
printf '\\f'
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
    # A scan: pages without text, short pages. FAKE_B5_PASS: OCR results
    # (wrapped contents) have enough text on every page.
    verify-pages) case "$(cat "$3")" in *\\(*) [ -n "${FAKE_B5_PASS:-}" ] ;; *) exit 1 ;; esac ;;
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
    _stub(stubs / "tesseract", "exit 0\n")
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


def _ocr_calls(box):
    """OCRmyPDF calls that read pages; the PaddleOCR readiness probe reads none."""
    return [call for call in box.calls("ocrmypdf") if "--paddle-check" not in call.split()]


def _ocr_flags(call):
    """The ocrmypdf flags that differ between the CLIs and modes."""
    words = call.split()
    return {flag for flag in ("--skip-text", "--force-ocr", "--clean", "--rotate-pages", "--deskew")
            if flag in words}


def failure_reason(result, keep=5):
    """The line the plugin shows as the failure reason (classifyOcrFailure):
    the last line starting with ❌ among the last `keep` non-empty lines of
    stdout, then of stderr, trimmed."""
    def tail(text):
        return [line.strip() for line in text.splitlines() if line.strip()][-keep:]
    lines = tail(result.stdout) + tail(result.stderr)
    return next((line for line in reversed(lines) if line.startswith("❌")), None)


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


@pytest.mark.parametrize("rc,shown", [("0", "1"), ("3", "1"), ("2", "?")])
def test_combine_page_count_survives_qpdf_warnings(box, rc, shown):
    """qpdf exits 3 when it printed the count with warnings."""
    _write(box.work, {"a.pdf": "a"})

    result = box.run("pdf-combine.sh", "out", npages_rc=rc)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Pages:    {shown}\n" in result.stdout, result.stdout


def test_combine_force_ocr(box):
    _write(box.work, {"a.pdf": "a"})

    box.run("pdf-combine.sh", "out", "--force-ocr")

    assert [_ocr_flags(c) for c in box.calls("ocrmypdf")] == [{"--force-ocr", "--rotate-pages", "--deskew"}]


def test_combine_text_only_keeps_the_pages(box, tmp_path):
    """--text-only (reprocess-raw --in-place, #180) adds only the text layer:
    no Ghostscript (MediaBox fix, downscale), no rotation, deskew or optimization."""
    _stub(tmp_path / "stubs" / "gs", 'echo "gs $*" >> "$FAKE_LOG"\nexit 1\n')
    # A pixel-sized page: without --text-only, fix_mediabox runs Ghostscript.
    _stub(tmp_path / "stubs" / "pdfinfo", 'printf "Pages: 1\\nPage size: 2439 x 3413 pts\\n"\n')
    _write(box.work, {"a.pdf": "a"})

    result = box.run("pdf-combine.sh", "out", "--text-only")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files()["out.pdf"] == "ocr(a)"
    assert box.calls("gs") == []
    [ocr] = box.calls("ocrmypdf")
    words = ocr.split()
    assert _ocr_flags(ocr) == {"--skip-text"}
    assert words[words.index("--optimize") + 1] == "0"


def test_combine_text_only_retries_without_split_or_deskew(box):
    _write(box.work, {"a.pdf": "a"})

    result = box.run("pdf-combine.sh", "out", "--text-only", "--engine", "tesseract",
                     bad_text="ocr(a)", apple="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert box.files()["out.pdf"] == "apple(a)"
    assert box.calls("column_tools split") == []
    for call in box.calls("ocrmypdf"):
        assert not {"--deskew", "--rotate-pages", "--clean"} & set(call.split()), call


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
@pytest.mark.parametrize("options,fake,reason", [
    ([], {"bad_text": "ocr(a) ocr(split(a))"},
     "❌ Quality gate failed: only 0 characters per page (min: 200) — no file written"),
    ([], {"ocr_fail": "1"}, "❌ OCR failed on every attempt — no file written"),
    (["--no-quality-gate"], {"ocr_fail": "1"}, "❌ OCR failed — no file written"),
    # The plugin reads stderr after stdout: the merge step's own line wins.
    (["--split-columns"], {"merge_fail": "1"}, "❌ column_tools.py merge failed"),
], ids=["quality-gate", "every-run-crashed", "ocr", "re-merge"])
def test_single_output_failure_names_its_cause_last(box, script, options, fake, reason):
    """#217: the plugin shows the last ❌ line; the CLI adds none after the cause."""
    _write(box.work, {"a.pdf": "a"})

    result = box.run(script, "out", *options, **fake)

    assert result.returncode == 1, result.stdout
    assert failure_reason(result) == reason, result.stdout + result.stderr


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

"""Page cases: stash, add and replay a page the user marked as wrong
(Issue #139).

No vault and no model: the source is a vector PDF built here, converted
through its text layer, and the "user" edits the preview file.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import fitz
import pytest

import cases
from conversion import ConversionRequest, convert_document

REPOSITORY = Path(__file__).resolve().parents[2]

HEADER = "Skript Schuldrecht Allgemeiner Teil"
BODY = [
    "Der Anspruch des Kaeufers auf Uebereignung der Sache ist entstanden.",
    "Der Verkaeufer haftet nach § 280 Abs. 1 BGB fuer den Schaden.",
    None,  # a gap, which assembly does not turn into a paragraph break
    "Rechtsfolge ist der Schadensersatz statt der ganzen Leis-",
    "tung, soweit der Kaeufer daran kein Interesse mehr hat.",
]
FIRST = "Der Anspruch des Kaeufers auf Uebereignung der Sache ist entstanden."
PRODUCED = (
    "%% S. 1 | textlayer %%\n\n" + FIRST + " Der Verkaeufer haftet nach "
    "§ 280 Abs. 1 BGB fuer den Schaden. Rechtsfolge ist der Schadensersatz "
    "statt der ganzen Leistung, soweit der Kaeufer daran kein Interesse "
    "mehr hat.")


def _write_pdf(path: Path, header=HEADER, body=BODY):
    """Two pages that share a running line above the same body."""
    with fitz.open() as doc:
        for _ in range(2):
            page = doc.new_page(width=600, height=800)
            if header:
                page.insert_text(fitz.Point(60, 40), header, fontsize=9)
            y = 200
            for line in body:
                if line is None:
                    y += 40
                    continue
                page.insert_text(fitz.Point(60, y), line, fontsize=11)
                y += 16
        doc.save(path)


def _convert(pdf: Path, output: Path):
    convert_document(ConversionRequest(pdf=pdf, output_dir=output), None)


@pytest.fixture
def vault(tmp_path):
    """A vault with one converted source; returns `(root, pdf, preview)`."""
    root = tmp_path / "vault"
    pdf = root / "raw" / "skript.pdf"
    pdf.parent.mkdir(parents=True)
    _write_pdf(pdf)
    _convert(pdf, root / "_ocr-preview")
    return root, pdf, root / "_ocr-preview" / "skript.md"


def _edit(preview: Path, old: str, new: str):
    text = preview.read_text(encoding="utf-8")
    assert old in text
    preview.write_text(text.replace(old, new, 1), encoding="utf-8")


def _split_first_sentence(preview: Path):
    """The user's correction: the first sentence is a paragraph of its own."""
    _edit(preview, FIRST + " ", FIRST + "\n\n")


def _states(root, **kwargs):
    return {outcome.id: outcome.state for outcome in cases.run([root], **kwargs)}


def _stored(preview, number=1):
    return json.loads(cases.case_path(preview, number).read_text(
        encoding="utf-8"))


# --- Capture ----------------------------------------------------------------

def test_a_marked_page_becomes_a_case_with_produced_and_expected_block(vault):
    root, pdf, preview = vault

    assert cases.stash(preview, 1) == "stashed"
    _split_first_sentence(preview)
    path, case, missing = cases.add(preview, 1, note="one paragraph too few",
                                    issue=130)

    assert path == root / "_ocr-preview" / ".cases" / "skript" / "p001.json"
    assert cases.case_id(path) == "skript/p001"
    assert _stored(preview) == case
    assert case["schema"] == 1
    assert case["pdf"] == str(pdf.resolve())
    assert len(case["pdf_sha256"]) == 64
    assert case["page"]["number"] == 1
    assert case["page"]["source"] == "textlayer"
    assert [line[0] for line in case["page"]["lines"]][0] == HEADER
    assert case["running_lines"] == [HEADER]
    assert case["produced"] == PRODUCED
    assert case["expected"] == PRODUCED.replace(FIRST + " ", FIRST + "\n\n")
    assert (case["note"], case["issue"]) == ("one paragraph too few", 130)
    assert (case["status"], case["fault_stage"]) == ("open", "assembly")
    assert missing == []
    assert not cases.stash_path(preview, 1).exists()


def test_a_stash_survives_a_rerun_and_supplies_the_produced_block(vault):
    root, pdf, preview = vault
    cases.stash(preview, 1)
    _edit(preview, "Der Anspruch", "Der erste Anspruch")

    # The rerun writes a block from other lines over the user's edit.
    _write_pdf(pdf, body=["Eine ganz andere Seite ist es nach dem erneuten "
                          "Lauf geworden.", *BODY[1:]])
    _convert(pdf, root / "_ocr-preview")
    assert cases.stash(preview, 1) == "kept"
    _, case, _ = cases.add(preview, 1)

    assert case["produced"] == PRODUCED
    assert case["page"]["lines"][1][0] == BODY[0]
    assert "Eine ganz andere Seite" in case["expected"]


def test_marking_without_a_stash_takes_the_produced_block_from_the_cache(vault):
    _, _, preview = vault
    _split_first_sentence(preview)

    _, case, _ = cases.add(preview, 1)

    assert case["produced"] == PRODUCED
    assert case["expected"] != PRODUCED


def test_marking_again_updates_the_expected_block_only(vault):
    _, _, preview = vault
    cases.stash(preview, 1)
    _split_first_sentence(preview)
    cases.add(preview, 1, note="first note", issue=130)

    # The block in the preview is the user's now: nothing to stash.
    assert cases.stash(preview, 1) == "held"
    _edit(preview, "Schaden. ", "Schaden.\n\n")
    _, case, _ = cases.add(preview, 1)

    assert case["produced"] == PRODUCED
    assert case["expected"].count("\n\n") == 3
    assert (case["note"], case["issue"], case["status"]) == (
        "first note", 130, "open")


def test_a_preview_without_a_cache_entry_or_page_cannot_be_captured(vault):
    root, _, preview = vault

    with pytest.raises(cases.CaseError, match="no page 9"):
        cases.stash(preview, 9)
    for entry in (root / "_ocr-preview" / ".cache" / "skript").iterdir():
        entry.unlink()
    with pytest.raises(cases.CaseError, match="no page-cache entry"):
        cases.stash(preview, 1)
    assert cases.main(["add", str(preview), "--page", "1"]) == 1


def test_a_source_that_changed_since_the_conversion_cannot_be_captured(vault):
    _, pdf, preview = vault
    _write_pdf(pdf, header="Another running line")

    with pytest.raises(cases.CaseError, match="changed since page 1"):
        cases.stash(preview, 1)


def test_a_relative_source_is_found_from_the_vault_root(vault, monkeypatch):
    root, pdf, preview = vault
    _edit(preview, json.dumps(str(pdf)), '"raw/skript.pdf"')
    monkeypatch.chdir(root.parent)

    assert cases.stash(preview, 1) == "stashed"
    assert _load_stash(preview)["pdf"] == str(pdf.resolve())


def _load_stash(preview, number=1):
    return json.loads(cases.stash_path(preview, number).read_text(
        encoding="utf-8"))


# --- Replay -----------------------------------------------------------------

def test_an_open_case_is_a_known_failure_and_names_its_blocks(vault, capsys):
    root, _, preview = vault
    _split_first_sentence(preview)
    cases.add(preview, 1, issue=130)

    (outcome,) = cases.run([root])

    assert (outcome.id, outcome.state, outcome.source) == (
        "skript/p001", "open", "current")
    assert outcome.differing == (
        "- expected block 1: " + FIRST[:60],
        "- expected block 2: Der Verkaeufer haftet nach § 280 Abs. 1 BGB "
        "fuer den Schaden",
        "+ replayed block 1: " + FIRST[:60],
    )
    assert cases.main(["run", str(root)]) == 0
    report = capsys.readouterr().out
    assert "open" in report and "skript/p001  #130" in report
    assert "1 page case(s): 1 open" in report


def test_open_to_now_matching_to_promoted_to_fixed(vault, capsys):
    root, _, preview = vault
    # Marked without a correction, the case matches as soon as it is replayed:
    # what a case looks like once its assembly bug is fixed.
    cases.add(preview, 1)

    assert _states(root) == {"skript/p001": "now matching"}
    assert _stored(preview)["status"] == "open"
    assert _states(root, promote=True) == {"skript/p001": "promoted"}
    assert _stored(preview)["status"] == "fixed"
    assert _states(root) == {"skript/p001": "fixed"}
    assert cases.main(["run", str(root)]) == 0
    assert "1 fixed" in capsys.readouterr().out


def test_a_fixed_case_that_differs_fails_the_run(vault, monkeypatch, capsys):
    root, _, preview = vault
    cases.add(preview, 1)
    cases.run([root], promote=True)
    produced = cases.page_block

    def regressed(lines, context, meta):
        return produced(lines[:2], context, meta)

    monkeypatch.setattr(cases, "page_block", regressed)

    assert _states(root) == {"skript/p001": "regressed"}
    assert cases.main(["run", str(root)]) == 1
    assert "regressed" in capsys.readouterr().out
    assert _stored(preview)["status"] == "fixed"


def test_running_lines_are_recomputed_from_the_source(vault, monkeypatch):
    root, _, preview = vault
    cases.add(preview, 1)
    assert _states(root) == {"skript/p001": "now matching"}

    # A change to running-line detection reaches the case: here it no longer
    # finds the header, which then stays in the block.
    monkeypatch.setattr(cases, "source_running_lines", lambda _pdf: frozenset())

    (outcome,) = cases.run([root])
    assert outcome.state == "open"
    assert outcome.differing[-1] == "+ replayed block 1: " + HEADER


def test_a_changed_source_is_stale_and_replays_the_frozen_lines(vault, capsys):
    root, pdf, preview = vault
    cases.add(preview, 1)
    _write_pdf(pdf, header=None)

    (outcome,) = cases.run([root])

    assert (outcome.state, outcome.source) == ("now matching", "stale")
    cases.main(["run", str(root)])
    assert "stale: source changed" in capsys.readouterr().out


def test_a_missing_source_falls_back_to_the_frozen_lines(vault, capsys):
    root, pdf, preview = vault
    cases.add(preview, 1)
    pdf.unlink()

    (outcome,) = cases.run([root])

    assert (outcome.state, outcome.source) == ("now matching", "missing")
    cases.main(["run", str(root)])
    assert "source not found" in capsys.readouterr().out


# --- Fault stage ------------------------------------------------------------

def test_a_word_no_recognized_line_holds_means_upstream(vault, capsys):
    root, _, preview = vault
    _edit(preview, "Uebereignung", "Übereignung")

    _, case, missing = cases.add(preview, 1)

    assert (case["fault_stage"], case["fault_stage_by"]) == (
        "upstream", "coverage")
    assert missing == ["übereignung"]
    (outcome,) = cases.run([root])
    assert (outcome.state, outcome.source, outcome.differing) == (
        "upstream", None, ())
    assert cases.main(["run", str(root), "--promote"]) == 0
    assert "(not replayed)" in capsys.readouterr().out
    assert _stored(preview)["status"] == "open"


def test_the_fault_stage_can_be_set_and_stays_when_marking_again(vault):
    root, _, preview = vault
    _edit(preview, "Uebereignung", "Übereignung")

    _, case, _ = cases.add(preview, 1, fault_stage="assembly")
    assert (case["fault_stage"], case["fault_stage_by"]) == ("assembly", "user")
    _, case, _ = cases.add(preview, 1, note="again")
    assert (case["fault_stage"], case["fault_stage_by"]) == ("assembly", "user")
    assert _states(root) == {"skript/p001": "open"}


def test_markup_and_a_hyphenated_word_are_covered_by_the_lines():
    case = {
        "page": {"lines": [["Rechtsfolge ist der Schadens-", None],
                           ["ersatz statt der Leistung. 1", None]]},
        "expected": "%% S. 1 | ocr %%\n\n#### Rechtsfolge\n\n"
                    "ist der **Schadensersatz** statt der Leistung.[^7]\n\n"
                    "[^7]: Palandt",
    }

    assert cases.uncovered_words(case, "") == ["palandt"]
    assert cases.uncovered_words(case, "Palandt") == []


# --- Run --------------------------------------------------------------------

def test_a_run_can_be_limited_to_the_cases_of_an_issue(vault):
    root, _, preview = vault
    cases.add(preview, 1, issue=130)
    cases.add(preview, 2, issue=14)

    assert set(_states(root)) == {"skript/p001", "skript/p002"}
    assert set(_states(root, issue=14)) == {"skript/p002"}
    assert _states(root, issue=99) == {}


def test_a_stash_is_dropped_once_its_preview_left_the_folder(vault, capsys):
    root, _, preview = vault
    cases.stash(preview, 1)
    cases.add(preview, 2)
    accepted = root / "_ocr-preview" / "_accepted"
    accepted.mkdir()

    assert cases.main(["run", str(root)]) == 0
    assert cases.stash_path(preview, 1).exists()
    preview.rename(accepted / preview.name)
    assert cases.main(["run", str(root)]) == 0

    assert not cases.stash_path(preview, 1).exists()
    assert cases.case_path(preview, 2).exists()
    assert "removed 1 stash(es)" in capsys.readouterr().out


def test_an_older_schema_is_upgraded_instead_of_rejected(vault, monkeypatch):
    root, _, preview = vault
    cases.add(preview, 1)
    monkeypatch.setattr(cases, "SCHEMA", 2)
    monkeypatch.setattr(cases, "_CASE_KEYS", (*cases._CASE_KEYS, "reviewer"))
    monkeypatch.setitem(cases._UPGRADES, 1,
                        lambda record: {**record, "reviewer": None})

    case = cases.load_case(cases.case_path(preview, 1))

    assert (case["schema"], case["reviewer"]) == (2, None)
    assert _stored(preview)["schema"] == 1
    assert _states(root, promote=True) == {"skript/p001": "promoted"}
    assert (_stored(preview)["schema"], _stored(preview)["status"]) == (
        2, "fixed")


def test_a_case_this_version_cannot_read_fails_the_run(vault, capsys):
    root, _, preview = vault
    cases.add(preview, 1)
    path = cases.case_path(preview, 1)
    path.write_text(json.dumps({**_stored(preview), "schema": 99}),
                    encoding="utf-8")
    cases.case_path(preview, 2).write_text("{", encoding="utf-8")

    assert _states(root) == {"skript/p001": "unreadable",
                             "skript/p002": "unreadable"}
    assert cases.main(["run", str(root)]) == 1
    assert "schema 99" in capsys.readouterr().out


def test_run_needs_a_folder_or_the_vault_root(vault, monkeypatch, capsys):
    root, _, preview = vault
    cases.add(preview, 1)
    monkeypatch.delenv("VAULT_ROOT", raising=False)

    assert cases.main(["run"]) == 2
    assert "VAULT_ROOT is unset" in capsys.readouterr().err
    monkeypatch.setenv("VAULT_ROOT", str(root))
    assert cases.main(["run"]) == 0
    assert "skript/p001" in capsys.readouterr().out
    assert cases.main(["run", str(root / "missing")]) == 1


# --- Entry points -----------------------------------------------------------

def _clean_environment():
    """The environment without a vault and without the outer make's flags."""
    return {key: value for key, value in os.environ.items()
            if key not in ("VAULT_ROOT", "MAKEFLAGS", "MFLAGS",
                           "MAKEOVERRIDES", "MAKELEVEL")}


@pytest.mark.slow
def test_the_case_subcommand_is_reached_through_pdf2md(vault):
    root, _, preview = vault
    script = [sys.executable, str(REPOSITORY / "pdf2md" / "pdf2md.py"), "case"]

    def call(*arguments):
        return subprocess.run([*script, *arguments], capture_output=True,
                              text=True, env=_clean_environment())

    stashed = call("stash", str(preview), "--page", "1")
    _split_first_sentence(preview)
    added = call("add", str(preview), "--page", "1", "--note", "split",
                 "--issue", "130")
    replayed = call("run", str(root), "--issue", "130")

    assert (stashed.returncode, stashed.stdout) == (
        0, "stashed: skript/p001\n")
    assert (added.returncode, added.stdout) == (
        0, "case skript/p001: open, fault stage assembly\n")
    assert replayed.returncode == 0
    assert "1 page case(s): 1 open" in replayed.stdout
    assert call("run").returncode == 2


@pytest.mark.slow
def test_make_check_cases_stops_without_a_vault_root():
    result = subprocess.run(
        ["make", "check-cases"], cwd=REPOSITORY, capture_output=True,
        text=True, env=_clean_environment())

    assert result.returncode != 0
    assert "VAULT_ROOT is unset" in result.stdout


@pytest.mark.slow
def test_make_check_cases_replays_the_cases_of_the_vault(vault):
    root, _, preview = vault
    cases.add(preview, 1, issue=130)

    result = subprocess.run(
        ["make", "check-cases", f"VAULT_ROOT={root}", "ISSUE=130",
         f"CASES_PYTHON={sys.executable}"],
        cwd=REPOSITORY, capture_output=True, text=True,
        env=_clean_environment())

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 page case(s): 1 now matching" in result.stdout

"""Page cases: stash, add and replay a page the user marked as wrong
(Issue #139).

No vault and no model: the source is a vector PDF built here, converted
through its text layer, and the "user" edits the preview file.
"""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import fitz
import pytest

import cases
import page_cache
from assembly import AssemblyContext
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


def _write_pdf(path: Path, header=HEADER, body=BODY, footer=None):
    """Two pages that share a running line above the same body."""
    with fitz.open() as doc:
        for _ in range(2):
            page = doc.new_page(width=600, height=800)
            if header:
                page.insert_text(fitz.Point(60, 40), header, fontsize=9)
            if footer:
                page.insert_text(fitz.Point(60, 770), footer, fontsize=9)
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

def _rerun_with_other_lines(root, pdf):
    _write_pdf(pdf, body=["Eine ganz andere Seite ist es nach dem erneuten "
                          "Lauf geworden.", *BODY[1:]])
    _convert(pdf, root / "_ocr-preview")


def _load_stash(preview, number=1):
    return json.loads(cases.stash_path(preview, number).read_text(
        encoding="utf-8"))


def test_a_marked_page_becomes_a_case_with_produced_and_expected_block(vault):
    root, pdf, preview = vault

    assert cases.stash(preview, 1) == "stashed"
    _split_first_sentence(preview)
    path, case, missing = cases.add(preview, 1, note="one paragraph too few",
                                    issue=130)

    assert path == (root / "_ocr-preview" / ".cases" / "skript"
                    / "p001.json").resolve()
    assert cases.case_id(path) == "skript/p001"
    assert _stored(preview) == case
    assert case["schema"] == 2
    assert case["pdf"] == str(pdf.resolve())
    assert len(case["pdf_sha256"]) == 64
    assert case["page"]["number"] == 1
    assert case["page"]["source"] == "textlayer"
    assert page_cache.recognized_lines(case["page"])[0].text == HEADER
    assert [(line["text"], line["reason"]) for line in case["discarded"]] \
        == [(HEADER, "running_line")]
    assert case["running_lines"] == [HEADER]
    assert case["footer_lines"] == []
    assert case["produced"] == PRODUCED
    assert case["expected"] == PRODUCED.replace(FIRST + " ", FIRST + "\n\n")
    assert (case["note"], case["issue"]) == ("one paragraph too few", 130)
    assert (case["status"], case["fault_stage"]) == ("open", "assembly")
    assert missing == []
    assert not cases.stash_path(preview, 1).exists()


def test_a_rerun_that_reads_the_same_lines_keeps_the_stash(vault):
    root, pdf, preview = vault
    cases.stash(preview, 1)
    before = cases.stash_path(preview, 1).read_bytes()
    _edit(preview, "Der Anspruch", "Der erste Anspruch")

    _convert(pdf, root / "_ocr-preview")  # writes over the user's edit

    assert cases.stash(preview, 1) == "kept"
    assert cases.stash_path(preview, 1).read_bytes() == before


def test_a_stash_of_other_lines_is_replaced(vault):
    """The stash of an earlier version of the page — left by a rerun that
    read other lines, or by a preview that was accepted and converted again —
    must not supply the lines for the block the preview holds now."""
    root, pdf, preview = vault
    cases.stash(preview, 1)
    _rerun_with_other_lines(root, pdf)

    assert cases.stash(preview, 1) == "stashed"
    assert "Eine ganz andere Seite" in _load_stash(preview)["produced"]


def test_marking_ignores_a_stash_of_other_lines(vault):
    root, pdf, preview = vault
    cases.stash(preview, 1)
    _rerun_with_other_lines(root, pdf)
    _edit(preview, "andere Seite", "neue Seite")

    _, case, missing = cases.add(preview, 1)

    assert page_cache.recognized_lines(case["page"])[1].text.startswith(
        "Eine ganz andere Seite")
    assert "Eine ganz andere Seite" in case["produced"]
    assert "Eine ganz neue Seite" in case["expected"]
    assert missing == ["neue"]
    assert not cases.stash_path(preview, 1).exists()


def test_the_produced_block_is_the_replay_not_an_edited_preview(vault):
    _, _, preview = vault
    _split_first_sentence(preview)  # edited before anything was stashed

    assert cases.stash(preview, 1) == "stashed"

    assert _load_stash(preview)["produced"] == PRODUCED


def test_marking_without_a_stash_takes_the_lines_from_the_cache(vault):
    _, _, preview = vault
    _split_first_sentence(preview)

    _, case, _ = cases.add(preview, 1)

    assert case["produced"] == PRODUCED
    assert case["expected"] != PRODUCED


def test_marking_again_updates_the_expected_block_only(vault):
    _, _, preview = vault
    cases.stash(preview, 1)
    _split_first_sentence(preview)
    _, first, _ = cases.add(preview, 1, note="first note", issue=130)

    # The block in the preview is the user's now: nothing to stash.
    assert cases.stash(preview, 1) == "held"
    _edit(preview, "Schaden. ", "Schaden.\n\n")
    _, case, _ = cases.add(preview, 1)

    assert case["produced"] == PRODUCED
    assert case["page"] == first["page"]
    assert case["expected"].count("\n\n") == 3
    assert (case["note"], case["issue"], case["status"]) == (
        "first note", 130, "open")


def test_marking_again_after_a_rerun_takes_the_new_lines(vault):
    root, pdf, preview = vault
    _split_first_sentence(preview)
    cases.add(preview, 1, note="first note", issue=130)

    _rerun_with_other_lines(root, pdf)
    _edit(preview, "geworden. ", "geworden.\n\n")
    _, case, missing = cases.add(preview, 1)

    assert page_cache.recognized_lines(case["page"])[1].text.startswith(
        "Eine ganz andere Seite")
    assert "Eine ganz andere Seite" in case["produced"]
    assert (case["fault_stage"], missing) == ("assembly", [])
    assert (case["note"], case["issue"], case["status"]) == (
        "first note", 130, "open")


def test_marking_again_works_from_the_case_once_the_cache_is_gone(vault):
    root, _, preview = vault
    _split_first_sentence(preview)
    _, first, _ = cases.add(preview, 1)
    for entry in (root / "_ocr-preview" / ".cache" / "skript").iterdir():
        entry.unlink()

    _edit(preview, "Schaden. ", "Schaden.\n\n")
    _, case, _ = cases.add(preview, 1)

    assert case["page"] == first["page"]
    assert case["expected"].count("\n\n") == 3


def test_a_stash_this_version_cannot_read_does_not_block_marking(vault):
    _, _, preview = vault
    cases.stash(preview, 1)
    cases.stash_path(preview, 1).write_text("{", encoding="utf-8")
    _split_first_sentence(preview)

    assert cases.stash(preview, 1) == "stashed"
    cases.stash_path(preview, 1).write_text("{", encoding="utf-8")
    _, case, _ = cases.add(preview, 1)

    assert case["produced"] == PRODUCED
    assert not cases.stash_path(preview, 1).exists()


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


# --- Replay -----------------------------------------------------------------

@pytest.fixture
def corrected(vault):
    """The vault with page 1 corrected and marked: an open case."""
    root, _, preview = vault
    _split_first_sentence(preview)
    cases.add(preview, 1, issue=130)
    return vault


def _fix_assembly(monkeypatch):
    """Stand in for the assembly fix the corrected case waits for."""
    produced = cases.page_block

    def fixed(lines, context, meta):
        block = produced(lines, context, meta)
        return replace(block, markdown=block.markdown.replace(
            FIRST + " ", FIRST + "\n\n"))

    monkeypatch.setattr(cases, "page_block", fixed)


def test_an_open_case_is_a_known_failure_and_names_its_blocks(
        corrected, capsys):
    root, _, _ = corrected

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


def test_open_to_now_matching_to_promoted_to_fixed(
        corrected, monkeypatch, capsys):
    root, _, preview = corrected
    assert _states(root, promote=True) == {"skript/p001": "open"}

    _fix_assembly(monkeypatch)

    assert _states(root) == {"skript/p001": "now matching"}
    assert _stored(preview)["status"] == "open"
    assert _states(root, promote=True) == {"skript/p001": "promoted"}
    assert _stored(preview)["status"] == "fixed"
    assert _states(root) == {"skript/p001": "fixed"}
    assert cases.main(["run", str(root)]) == 0
    assert "1 fixed" in capsys.readouterr().out


def test_a_fixed_case_that_differs_fails_the_run(
        corrected, monkeypatch, capsys):
    root, _, preview = corrected
    with monkeypatch.context() as patch:
        _fix_assembly(patch)
        cases.run([root], promote=True)

    # The fix is gone again: the case regressed.
    assert _states(root) == {"skript/p001": "regressed"}
    assert cases.main(["run", str(root)]) == 1
    assert "regressed" in capsys.readouterr().out
    assert _stored(preview)["status"] == "fixed"


def test_a_page_marked_without_a_correction_is_never_promoted(vault, capsys):
    root, _, preview = vault
    cases.add(preview, 1)
    # Bold is nothing the comparer sees, so this is no correction either.
    _edit(preview, "%% S. 2 | textlayer %%\n\nDer Anspruch",
          "%% S. 2 | textlayer %%\n\n**Der Anspruch**")
    cases.add(preview, 2)

    assert _states(root, promote=True) == {"skript/p001": "uncorrected",
                                           "skript/p002": "uncorrected"}
    assert _stored(preview)["status"] == "open"
    assert cases.main(["run", str(root)]) == 0
    assert "correct the page and mark it again" in capsys.readouterr().out


def test_running_lines_are_recomputed_from_the_source(corrected, monkeypatch):
    root, _, _ = corrected
    _fix_assembly(monkeypatch)
    assert _states(root) == {"skript/p001": "now matching"}

    # A change to running-line detection reaches the case: here it no longer
    # finds the header, which then stays in the block.
    monkeypatch.setattr(cases, "source_assembly_context",
                        lambda _pdf: AssemblyContext())

    (outcome,) = cases.run([root])
    assert outcome.state == "open"
    assert outcome.differing == ("+ replayed block 1: " + HEADER,)


def test_a_case_freezes_its_footer_lines_for_a_stale_source(tmp_path):
    footer = "Kursreihe Musterrecht - 2026"
    root = tmp_path / "vault"
    pdf = root / "raw" / "skript.pdf"
    pdf.parent.mkdir(parents=True)
    _write_pdf(pdf, footer=footer)
    _convert(pdf, root / "_ocr-preview")
    preview = root / "_ocr-preview" / "skript.md"
    _split_first_sentence(preview)

    _, case, _ = cases.add(preview, 1)

    assert case["running_lines"] == sorted([footer, HEADER])
    assert case["footer_lines"] == [footer]
    _write_pdf(pdf, header=None)
    context, state = cases._Sources().context(case)
    assert state == "stale"
    assert (context.running_lines, context.footer_lines) == (
        {footer, HEADER}, {footer})


def test_a_changed_source_is_stale_and_replays_the_frozen_lines(
        corrected, monkeypatch, capsys):
    root, pdf, _ = corrected
    _fix_assembly(monkeypatch)
    _write_pdf(pdf, header=None)

    (outcome,) = cases.run([root])

    assert (outcome.state, outcome.source) == ("now matching", "stale")
    cases.main(["run", str(root)])
    assert "stale: source changed" in capsys.readouterr().out


def test_a_missing_source_falls_back_to_the_frozen_lines(
        corrected, monkeypatch, capsys):
    root, pdf, _ = corrected
    _fix_assembly(monkeypatch)
    pdf.unlink()

    (outcome,) = cases.run([root])

    assert (outcome.state, outcome.source) == ("now matching", "missing")
    cases.main(["run", str(root)])
    assert "source not found" in capsys.readouterr().out


def test_promoting_freezes_the_running_lines_the_case_matched_with(
        corrected, monkeypatch):
    """A case captured while running-line detection was wrong must stay
    fixed once its source is gone."""
    root, pdf, preview = corrected
    path = cases.case_path(preview, 1)
    path.write_text(json.dumps({**_stored(preview), "running_lines": [],
                                "footer_lines": [HEADER]}),
                    encoding="utf-8")
    _fix_assembly(monkeypatch)

    assert _states(root, promote=True) == {"skript/p001": "promoted"}
    assert _stored(preview)["running_lines"] == [HEADER]
    assert _stored(preview)["footer_lines"] == []
    pdf.unlink()
    assert _states(root) == {"skript/p001": "fixed"}


def test_a_footnote_mark_in_another_paragraph_differs():
    expected = "%% S. 1 | ocr %%\n\nErster Absatz.[^1]\n\nZweiter Absatz."
    moved = "%% S. 1 | ocr %%\n\nErster Absatz.\n\nZweiter Absatz.[^1]"

    assert cases.compare(expected, expected) == []
    assert cases.compare(expected, moved) == [
        "- expected block 1: Erster Absatz.[^1]",
        "- expected block 2: Zweiter Absatz.",
        "+ replayed block 1: Erster Absatz.",
        "+ replayed block 2: Zweiter Absatz.[^1]",
    ]


def test_a_block_with_a_footnote_number_twice_matches_itself():
    block = ("%% S. 1 | ocr %%\n\nText.[^1]\n\n[^1]: Links, Rn. 5.\n\n"
             "[^1]: Rechts, Rn. 7.")

    assert cases.compare(block, block) == []


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
    # Lines as a case from before Issue #144 holds them.
    case = {
        "page": {"lines": [["Rechtsfolge ist der Schadens-", None],
                           # a decomposed umlaut, as some text layers hold it
                           ["ersatz statt der Rückgabe. 1", None]]},
        "expected": "%% S. 1 | ocr %%\n\n#### Rechtsfolge\n\n"
                    "ist der **Schadensersatz** statt der Rückgabe.[^7]\n\n"
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


def test_overlapping_folders_replay_a_case_once(corrected, monkeypatch, capsys):
    root, _, _ = corrected
    monkeypatch.setenv("HOME", str(root.parent))

    assert len(cases.run([root, root / "_ocr-preview"])) == 1
    # An unexpanded tilde, as `make check-cases VAULT_ROOT=~/vault` passes it.
    assert cases.main(["run", "~/vault"]) == 0
    assert "1 page case(s): 1 open" in capsys.readouterr().out


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


def test_an_older_schema_is_upgraded_instead_of_rejected(
        corrected, monkeypatch):
    root, _, preview = corrected
    current = cases.SCHEMA
    monkeypatch.setattr(cases, "SCHEMA", current + 1)
    monkeypatch.setattr(cases, "_CASE_KEYS", (*cases._CASE_KEYS, "reviewer"))
    monkeypatch.setitem(cases._UPGRADES, current,
                        lambda record: {**record, "reviewer": None})

    case = cases.load_case(cases.case_path(preview, 1))

    assert (case["schema"], case["reviewer"]) == (current + 1, None)
    assert _stored(preview)["schema"] == current
    # Marking again writes the upgraded record, with the key the newer
    # schema added.
    _edit(preview, "Schaden. ", "Schaden.\n\n")
    cases.add(preview, 1)
    assert (_stored(preview)["schema"], _stored(preview)["reviewer"]) == (
        current + 1, None)
    assert _states(root) == {"skript/p001": "open"}


def test_a_schema_1_case_counts_all_running_lines_as_footer_lines(corrected):
    """Schema 1 did not tell footer lines apart; all of them counted."""
    _, _, preview = corrected
    path = cases.case_path(preview, 1)
    record = {key: value for key, value in _stored(preview).items()
              if key != "footer_lines"}
    path.write_text(json.dumps({**record, "schema": 1}), encoding="utf-8")

    case = cases.load_case(path)

    assert (case["schema"], case["footer_lines"]) == (2, [HEADER])


def test_a_case_this_version_cannot_read_fails_the_run(corrected, capsys):
    root, _, preview = corrected
    path = cases.case_path(preview, 1)
    path.write_text(json.dumps({**_stored(preview), "schema": 99}),
                    encoding="utf-8")
    cases.case_path(preview, 2).write_text("{", encoding="utf-8")

    assert _states(root) == {"skript/p001": "unreadable",
                             "skript/p002": "unreadable"}
    assert cases.main(["run", str(root)]) == 1
    assert "schema 99" in capsys.readouterr().out


def test_a_case_that_cannot_be_replayed_fails_the_run_not_the_others(
        corrected, capsys):
    root, _, preview = corrected
    _edit(preview, "%% S. 2 | textlayer %%\n\n" + FIRST + " ",
          "%% S. 2 | textlayer %%\n\n" + FIRST + "\n\n")
    cases.add(preview, 2)
    damaged = _stored(preview)
    del damaged["page"]["source"]
    cases.case_path(preview, 1).write_text(json.dumps(damaged),
                                           encoding="utf-8")

    assert _states(root) == {"skript/p001": "error", "skript/p002": "open"}
    assert cases.main(["run", str(root)]) == 1
    assert "replay failed: KeyError('source')" in capsys.readouterr().out


def test_run_needs_a_folder_or_the_vault_root(corrected, monkeypatch, capsys):
    root, _, _ = corrected
    monkeypatch.delenv("VAULT_ROOT", raising=False)

    assert cases.main(["run"]) == 2
    assert "VAULT_ROOT is unset" in capsys.readouterr().err
    monkeypatch.setenv("VAULT_ROOT", str(root))
    assert cases.main(["run"]) == 0
    assert "skript/p001" in capsys.readouterr().out
    assert cases.main(["run", str(root / "missing")]) == 1


# --- What the plugin reads (contracts/cli-contract.json, Issue #140) ---------

CONTRACT = json.loads((REPOSITORY / "contracts" / "cli-contract.json")
                      .read_text(encoding="utf-8"))
PAGE_CASES = CONTRACT["pageCases"]


def test_list_names_the_cases_of_a_preview_in_page_order(corrected, capsys):
    _, _, preview = corrected
    _edit(preview, "%% S. 2 | textlayer %%\n\n" + FIRST + " ",
          "%% S. 2 | textlayer %%\n\n" + FIRST + " Bereicherungsrecht ")
    cases.add(preview, 2)
    cases.stash_path(preview, 1).parent.mkdir()
    cases.stash_path(preview, 1).write_text("{}", encoding="utf-8")
    capsys.readouterr()

    assert cases.main(["list", str(preview)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "case skript/p001: open, fault stage assembly",
        "case skript/p002: open, fault stage upstream"]


def test_list_needs_neither_cases_nor_the_preview(vault, capsys):
    root, _, preview = vault

    assert cases.main(["list", str(preview)]) == 0
    assert cases.main(["list", str(root / "_accepted" / "skript.md")]) == 0
    assert capsys.readouterr().out == ""


def test_list_leaves_out_a_case_it_cannot_read_and_says_so(corrected, capsys):
    _, _, preview = corrected
    cases.case_path(preview, 2).write_text("{", encoding="utf-8")

    assert cases.main(["list", str(preview)]) == 0
    printed = capsys.readouterr()
    assert printed.out.splitlines() == [
        "case skript/p001: open, fault stage assembly"]
    assert "p002.json" in printed.err


@pytest.mark.parametrize("line", PAGE_CASES["caseLines"])
def test_the_case_line_is_the_one_the_contract_pins(line):
    path = cases.case_path(Path(PAGE_CASES["list"]["args"][2]), line["page"])

    assert cases.case_line(path, {"status": line["status"],
                                  "fault_stage": line["faultStage"]}
                           ) == line["stdout"]
    assert line["faultStage"] in cases.FAULT_STAGES


def test_the_plugins_calls_run_as_the_contract_pins_them(
        vault, monkeypatch, capsys):
    """stash, add, list and a failing call with the contract's own argv,
    from the vault root as the plugin spawns them."""
    root, _, preview = vault
    monkeypatch.chdir(root)
    codes = CONTRACT["exitCodes"]
    wanted = PAGE_CASES["caseLines"][0]["stdout"]

    def call(name):
        arguments = PAGE_CASES[name]["args"]
        assert arguments[0] == "case"
        code = cases.main(arguments[1:])
        return code, capsys.readouterr()

    assert call("list") == (codes["success"], ("", ""))
    assert call("stash")[0] == codes["success"]
    _split_first_sentence(preview)
    code, printed = call("add")
    assert code == codes["success"]
    assert printed.out.splitlines()[0] == wanted
    assert _stored(preview)["note"] == PAGE_CASES["add"]["note"]
    code, printed = call("addWithoutNote")
    assert (code, printed.out.splitlines()[0]) == (codes["success"], wanted)
    assert _stored(preview)["note"] == PAGE_CASES["add"]["note"]
    code, printed = call("list")
    assert (code, printed.out.splitlines()) == (codes["success"], [wanted])

    code, printed = call("failure")
    assert code == codes[PAGE_CASES["failure"]["exitCode"]]
    assert printed.err.splitlines() == PAGE_CASES["failure"]["stderr"]


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

    # `endswith`: a newer pymupdf announces itself on stdout first.
    assert stashed.returncode == 0
    assert stashed.stdout.endswith("stashed: skript/p001\n")
    assert added.returncode == 0
    assert added.stdout.endswith(
        "case skript/p001: open, fault stage assembly\n")
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
def test_make_check_cases_replays_the_cases_of_the_vault(corrected):
    root, _, _ = corrected

    result = subprocess.run(
        ["make", "check-cases", f"VAULT_ROOT={root}", "ISSUE=130",
         f"CASES_PYTHON={sys.executable}"],
        cwd=REPOSITORY, capture_output=True, text=True,
        env=_clean_environment())

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 page case(s): 1 open" in result.stdout


def test_a_page_rewritten_in_the_new_line_format_is_the_same_page():
    """A cache entry from before Issue #144 and its rewrite hold the same
    lines: a stash made from one still belongs to the other."""
    old = {"number": 1, "source": "textlayer", "mode": "textlayer",
           "lines": [["Text", [1, 2, 3, 4]]], "columns": [0]}
    new = {"number": 1, "source": "textlayer", "mode": "textlayer",
           "line_format": 2,
           "lines": [{"text": "Text", "box": [1, 2, 3, 4], "column": 0}]}
    assert cases._same_page({"page": old, "pdf_sha256": "x"},
                            {"page": new, "pdf_sha256": "x"})
    assert not cases._same_page({"page": old, "pdf_sha256": "x"},
                                {"page": {**new, "number": 2},
                                 "pdf_sha256": "x"})

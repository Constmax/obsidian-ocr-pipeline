"""Producer side of the CLI contract with the plugin (Issue #55).

contracts/cli-contract.json and contracts/progress-v1.jsonl are read by
these tests and by plugin/test/cli-contract.test.ts. A key, an exit code or a
format changed on only one side turns one of the two suites red.
"""
import json
import subprocess
import sys
from pathlib import Path

import fitz
import pytest

import assembly
import conversion
import pdf2md as pdf2md_cli

REPO = Path(__file__).resolve().parent.parent.parent
CONTRACTS = REPO / "contracts"
CONTRACT = json.loads((CONTRACTS / "cli-contract.json").read_text(encoding="utf-8"))
PROGRESS = CONTRACT["progress"]
FIXTURE_LINES = (CONTRACTS / PROGRESS["fixture"]).read_text(
    encoding="utf-8").splitlines()

TYPES = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: (isinstance(value, (int, float))
                             and not isinstance(value, bool)),
    "boolean": lambda value: isinstance(value, bool),
}


def contract_violations(event):
    """What makes `event` break the contract; empty when it is valid.

    The producer is held to the declared fields only. Consumers tolerate
    unknown ones, but a new field belongs in the contract first.
    """
    spec = PROGRESS["events"].get(event.get("typ"))
    if spec is None:
        return [f"unknown event type {event.get('typ')!r}"]
    problems = []
    for key, kind in spec["required"].items():
        if key not in event:
            problems.append(f"missing {key}")
        elif not TYPES[kind](event[key]):
            problems.append(f"{key} is not {kind}")
    for key, kind in spec["optional"].items():
        if key in event and not TYPES[kind](event[key]):
            problems.append(f"{key} is not {kind}")
    undeclared = set(event) - set(spec["required"]) - set(spec["optional"])
    problems += [f"undeclared field {key}" for key in sorted(undeclared)]
    if event.get("protokoll") != PROGRESS["protocol"]:
        problems.append(f"protokoll {event.get('protokoll')!r}")
    if event["typ"] == "seite" and event.get("herkunft") not in PROGRESS["origins"]:
        problems.append(f"herkunft {event.get('herkunft')!r}")
    return problems


def _build(event):
    """Rebuild a fixture event through the producer's own event builders."""
    if event["typ"] == "start":
        return pdf2md_cli.start_event(event["datei"], event["seiten"], event["dpi"])
    if event["typ"] == "seite":
        return pdf2md_cli.page_event(
            event["nr"], event["von"], event["sekunden"], event["herkunft"],
            event["entgleist"], event.get("grund"))
    return pdf2md_cli.finished_event(
        event["ziel"], event["sekunden"], event["entgleist"])


@pytest.mark.parametrize("line", FIXTURE_LINES)
def test_the_canonical_fixture_follows_the_contract(line):
    assert contract_violations(json.loads(line)) == []


@pytest.mark.parametrize("line", FIXTURE_LINES)
def test_the_producer_emits_the_canonical_fixture_byte_for_byte(line):
    assert json.dumps(_build(json.loads(line)), ensure_ascii=False) == line


def test_the_fixture_covers_every_event_type_and_origin():
    events = [json.loads(line) for line in FIXTURE_LINES]
    assert {event["typ"] for event in events} == set(PROGRESS["events"])
    assert {event["herkunft"] for event in events
            if event["typ"] == "seite"} == set(PROGRESS["origins"])
    assert any("grund" in event for event in events)


def test_a_real_run_emits_start_pages_finished_in_contract_form(tmp_path):
    pdf = tmp_path / "vector.pdf"
    with fitz.open() as document:
        for number in range(1, 4):
            document.new_page(width=600, height=800).insert_text(
                fitz.Point(20, 100), f"Page {number} " + "x" * 180, fontsize=5)
        document.save(pdf)

    result = subprocess.run(
        [sys.executable, str(REPO / "pdf2md" / "pdf2md.py"), str(pdf),
         "--fortschritt", "--diagramm-seiten", "3", "--out", str(tmp_path / "out")],
        capture_output=True, text=True, timeout=120, check=False)

    assert result.returncode == CONTRACT["exitCodes"]["success"], result.stderr
    events = [json.loads(line) for line in result.stderr.splitlines()
              if line.startswith("{")]
    assert [event["typ"] for event in events] == [
        "start", "seite", "seite", "seite", "fertig"]
    assert {event["herkunft"] for event in events[1:4]} == {"textlayer", "diagramm"}
    for event in events:
        assert contract_violations(event) == [], event


def test_exit_codes_match_the_contract():
    codes = CONTRACT["exitCodes"]
    assert pdf2md_cli.EXIT_USAGE == codes["usage"]
    assert pdf2md_cli.EXIT_CHECK == codes["check-failed"]
    assert pdf2md_cli.EXIT_CANCELLED_PARTIAL == codes["cancelled-partial"]
    assert pdf2md_cli.EXIT_CANCELLED_EMPTY == codes["cancelled-empty"]


def test_input_suffixes_match_the_contract():
    assert conversion.INPUT_SUFFIXES == frozenset(CONTRACT["inputSuffixes"])


def test_preview_format_version_matches_the_contract():
    assert assembly.PREVIEW_FORMAT == CONTRACT["previewFormat"]
    frontmatter = assembly.build_frontmatter(
        title="t", source_pdf_path=Path("t.pdf"), pages=1, pages_textlayer=1,
        pages_ocr=0, ocr_date="2026-09-24", ocr_timestamp="2026-09-24T12:00:00")
    assert f"vorschau-format: {CONTRACT['previewFormat']}\n" in frontmatter


def test_a_title_with_colon_space_gives_valid_yaml():
    yaml = pytest.importorskip("yaml")
    frontmatter = assembly.build_frontmatter(
        title="Fall 8: Anfechtung", source_pdf_path=Path("raw/Fall 8: Anfechtung.pdf"),
        pages=1, pages_textlayer=1, pages_ocr=0,
        ocr_date="2026-09-24", ocr_timestamp="2026-09-24T12:00:00")

    fields = yaml.safe_load(frontmatter.strip().strip("-"))

    assert fields["titel"] == "Fall 8: Anfechtung"
    assert fields["quelle-pdf"] == "raw/Fall 8: Anfechtung.pdf"

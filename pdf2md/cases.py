#!/usr/bin/env python3
"""Page cases: a page the user marked as wrong, replayable without the model
(Issue #139; terms in CONTEXT.md).

A page case keeps the recognized lines of one page together with the page
block Stage 2 produced from them and the page block the user expects. A
replay puts the lines through `conversion.page_block` again and compares the
result with the expected block, so an assembly fix can be checked against the
pages it was made for.

Cases live beside the preview, never in the repository (they hold page text):

    <preview folder>/.cases/<stem>/pNNN.json          the case
    <preview folder>/.cases/<stem>/.stash/pNNN.json   the stash

This module owns that format. The plugin spawns `pdf2md case stash | add |
list` and knows neither the page-cache path nor the case format; what it
reads of their output is pinned in contracts/cli-contract.json.

The dictionary pass is not part of a replay: it depends on the word lists of
the machine, and a word it corrected was recognized wrongly — an upstream
fault.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

import page_cache
from assembly import (PREVIEW_MARKER, AssemblyContext, PreviewFormatError,
                      split_preview)
from conversion import (BlockContext, PageBlock, PageMeta,
                        diagram_image_name, page_block,
                        source_assembly_context)

SCHEMA = 2
CASES_DIR = ".cases"
STASH_DIR = ".stash"
FAULT_STAGES = ("assembly", "upstream")

# Upgrades by the schema they start from; each returns the record as the next
# schema holds it. The loader applies them in turn, so an older case is
# upgraded instead of rejected.
_UPGRADES: dict[int, Callable[[dict], dict]] = {
    # Schema 2 names the footer lines among the running lines (Issue #161).
    # A schema-1 record does not tell them apart, so all of them count, as
    # they did when it was written.
    1: lambda record: {**record,
                       "footer_lines": record.get("running_lines", [])},
}

_CASE_KEYS = ("pdf", "pdf_sha256", "page", "diagram_image",
              "diagram_image_only", "running_lines", "footer_lines",
              "produced", "expected", "note", "issue", "status",
              "fault_stage", "fault_stage_by")
_STASH_KEYS = _CASE_KEYS[:_CASE_KEYS.index("expected")]


class CaseError(Exception):
    """A case command that cannot do what was asked; the message says why."""


# --- Files ------------------------------------------------------------------

def cases_directory(preview: Path) -> Path:
    return preview.parent / CASES_DIR / preview.stem


def case_path(preview: Path, number: int) -> Path:
    return cases_directory(preview) / f"p{number:03d}.json"


def stash_path(preview: Path, number: int) -> Path:
    return cases_directory(preview) / STASH_DIR / f"p{number:03d}.json"


def case_id(path: Path) -> str:
    """How issues and pull requests name a case: `<stem>/pNNN`."""
    return f"{path.parent.name}/{path.stem}"


def _upgrade(record: dict) -> dict:
    schema = record.get("schema")
    if not isinstance(schema, int) or not 1 <= schema <= SCHEMA:
        raise CaseError(f"schema {schema!r} is not one this pdf2md reads "
                        f"(up to {SCHEMA})")
    while schema < SCHEMA:
        record = {**_UPGRADES[schema](record), "schema": schema + 1}
        schema += 1
    return record


def _load(path: Path, keys) -> dict:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CaseError(f"cannot read {path}: {error}") from None
    if not isinstance(record, dict):
        raise CaseError(f"cannot read {path}: not a case record")
    try:
        record = _upgrade(record)
    except CaseError as error:
        raise CaseError(f"cannot read {path}: {error}") from None
    missing = [key for key in keys if key not in record]
    if missing:
        raise CaseError(f"cannot read {path}: missing {', '.join(missing)}")
    return record


def load_case(path: Path) -> dict:
    return _load(path, _CASE_KEYS)


def _write(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    page_cache.write_text_atomic(
        path, json.dumps(record, ensure_ascii=False, indent=1) + "\n")


# --- Capture ----------------------------------------------------------------

def _preview_page(preview: Path, number: int):
    """The preview's frontmatter fields and its block of page `number`."""
    try:
        document = split_preview(preview.read_text(encoding="utf-8"))
    except OSError as error:
        raise CaseError(f"cannot read {preview}: {error}") from None
    except PreviewFormatError as error:
        raise CaseError(f"{preview} is not a preview: {error}") from None
    for page in document.pages:
        if page.number == number:
            return document.fields, page
    raise CaseError(f"{preview.name} has no page {number}")


def _source_pdf(preview: Path, fields: dict) -> Path:
    """The source the preview names in `quelle-pdf`.

    The field holds the path as pdf2md was given it, which is relative to
    the folder that run started in: usually the vault root, an ancestor of
    the preview folder.
    """
    value = fields.get("quelle-pdf", "")
    try:
        value = json.loads(value)
    except ValueError:
        pass
    if not isinstance(value, str) or not value:
        raise CaseError(f"{preview.name} names no source (quelle-pdf)")
    path = Path(value).expanduser()
    candidates = [path] if path.is_absolute() else [
        base / path for base in (Path.cwd(), *preview.resolve().parents)]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise CaseError(f"source not found: {value} (named by {preview.name})")


def _capture(preview: Path, fields: dict, page) -> dict:
    """What a case keeps of a page besides its blocks, read from the page
    cache and the source."""
    # A preview carries the stem of its source, which names the cache folder.
    directory = page_cache.cache_directory(preview.parent, preview)
    entry = page_cache.read_latest_page(directory, page.number)
    context = page_cache.read_latest_context(directory, page.number)
    if entry is None or context is None:
        raise CaseError(
            f"no page-cache entry for page {page.number} of {preview.name} "
            f"(looked in {directory}) — convert the page again")
    if page_cache.in_tile_coordinates(entry):
        raise CaseError(
            f"page {page.number} of {preview.name} was tiled before boxes "
            "were in page coordinates (its cache entry holds tile "
            "coordinates) — convert the page again")
    pdf = _source_pdf(preview, fields)
    if page_cache.file_sha256(pdf) != context["pdf_sha256"]:
        raise CaseError(
            f"{pdf.name} changed since page {page.number} was converted — "
            "convert the page again")
    return {
        "schema": SCHEMA,
        "pdf": str(pdf),
        "pdf_sha256": context["pdf_sha256"],
        "page": entry,
        "diagram_image": (diagram_image_name(pdf, page.number)
                          if page.origin == "diagramm" else None),
        "diagram_image_only": bool(
            context["parameters"].get("diagram_image_only")),
        **_frozen_lines(source_assembly_context(pdf)),
    }


def _frozen_lines(context: AssemblyContext) -> dict:
    """The running lines of a context as a case freezes them."""
    return {"running_lines": sorted(context.running_lines),
            "footer_lines": sorted(context.footer_lines)}


def _frozen_context(record: dict) -> AssemblyContext:
    """The running lines a stash or case froze. A footer line is one of the
    running lines, even in a record edited by hand."""
    running = frozenset(record["running_lines"])
    return AssemblyContext(running, running & frozenset(record["footer_lines"]))


def _same_page(record: dict | None, captured: dict) -> bool:
    """Whether a stash or case was made from the lines the page has now,
    whichever line format each entry holds them in."""
    def lines_and_rest(page):
        return (page_cache.recognized_lines(page),
                {key: value for key, value in page.items()
                 if key not in ("lines", "columns", "line_format")})

    return (record is not None
            and record["pdf_sha256"] == captured["pdf_sha256"]
            and lines_and_rest(record["page"]) == lines_and_rest(captured["page"]))


def _load_or_none(path: Path, keys) -> dict | None:
    """A stash or case, or None when there is none this version can read."""
    try:
        return _load(path, keys) if path.exists() else None
    except CaseError:
        return None


def _with_produced(captured: dict) -> dict:
    """A capture with its produced block: the replay of its lines, and the
    lines that replay discarded, each with its reason.

    Not the block the preview holds. That one may be edited already — an
    edit outside the review view, a stash that failed earlier — and nothing
    in the file tells. The replay is what Stage 2 makes of these lines.
    """
    block = _replay_block(captured, _frozen_context(captured))
    return {**captured, "produced": block.markdown,
            "discarded": [{**page_cache.line_json(line), "reason": reason}
                          for line, reason in block.discarded]}


def stash(preview: Path, number: int) -> str:
    """Keep the produced block of a page and the lines it was made from,
    called before the first edit of the page is saved.

    The block in a preview always comes from the page's current page-cache
    entry, so a stash or case made from other lines belongs to an earlier
    version of the page. Returns what happened:

    - `stashed`: the block and the page's recognized lines are kept.
    - `kept`: the page has a stash of these lines. It holds the first
      produced version since the last mark, and a rerun that reads the same
      lines again must not replace it.
    - `held`: the page's case was made from these lines, so it holds the
      produced block already; the block in the preview is the user's.
    """
    target = stash_path(preview, number)
    fields, page = _preview_page(preview, number)
    captured = _capture(preview, fields, page)
    if _same_page(_load_or_none(target, _STASH_KEYS), captured):
        return "kept"
    if _same_page(_load_or_none(case_path(preview, number), _CASE_KEYS),
                  captured):
        target.unlink(missing_ok=True)
        return "held"
    _write(target, _with_produced(captured))
    return "stashed"


def add(preview: Path, number: int, note: str | None = None,
        issue: int | None = None, fault_stage: str | None = None):
    """Mark a page as wrong: its current block is what the user expects.

    The lines and the produced block come from the stash; without one, from
    the case the page already has (marking again updates the expected
    block). Both count only while they were made from the lines the page
    has now: after a rerun that read other lines, the case is made anew from
    the current page-cache entry. When that entry or the source is gone, the
    stash or case is all there is and is used as it is.

    Returns `(path, case, missing)` — `missing` are the words that decided
    an automatic `upstream`.
    """
    fields, page = _preview_page(preview, number)
    target, stashed = case_path(preview, number), stash_path(preview, number)
    previous = load_case(target) if target.exists() else None
    stash_record = _load_or_none(stashed, _STASH_KEYS)
    try:
        captured = _capture(preview, fields, page)
    except CaseError:
        if stash_record is None and previous is None:
            raise
        base = stash_record or previous
    else:
        base = next((record for record in (stash_record, previous)
                     if _same_page(record, captured)), None)
        if base is None:
            base = _with_produced(captured)

    case = {**base, "schema": SCHEMA, "expected": page.text}
    kept = previous or {}
    case["note"] = note if note is not None else kept.get("note", "")
    case["issue"] = issue if issue is not None else kept.get("issue")
    unchanged = base is previous and previous["expected"] == page.text
    case["status"] = previous["status"] if unchanged else "open"

    missing = []
    if fault_stage is not None:
        case["fault_stage"], case["fault_stage_by"] = fault_stage, "user"
    elif kept.get("fault_stage_by") == "user":
        case["fault_stage"], case["fault_stage_by"] = kept["fault_stage"], "user"
    else:
        missing = uncovered_words(
            case, replay(case, _frozen_context(case)))
        case["fault_stage"] = "upstream" if missing else "assembly"
        case["fault_stage_by"] = "coverage"
    case["marked"] = datetime.now().isoformat(timespec="seconds")

    _write(target, case)
    stashed.unlink(missing_ok=True)
    return target, case, missing


def list_cases(preview: Path) -> tuple[list[tuple[Path, dict]], list[str]]:
    """The cases of a preview's pages in page order, and why others were
    left out.

    Reads the case files only: neither the preview nor the page cache is
    needed, so the review view can ask which pages to badge at any time.
    """
    def number(path: Path) -> int:
        digits = path.stem[1:]
        return int(digits) if digits.isdigit() else 0

    found, unreadable = [], []
    for path in sorted(cases_directory(preview).glob("p*.json"), key=number):
        try:
            found.append((path, load_case(path)))
        except CaseError as error:
            unreadable.append(str(error))
    return found, unreadable


# --- Replay and comparison --------------------------------------------------

def replay(case: dict, assembly: AssemblyContext) -> str:
    """The page block the current code makes of the case's lines."""
    return _replay_block(case, assembly).markdown


def _replay_block(case: dict, assembly: AssemblyContext) -> PageBlock:
    context = BlockContext(
        assembly=assembly,
        diagram_image_only=case["diagram_image_only"])
    meta = PageMeta.from_cache_entry(case["page"], case["diagram_image"])
    return page_block(page_cache.recognized_lines(case["page"]), context, meta)


def _paragraphs(block: str) -> list[str]:
    """The paragraphs of a page block, its marker line excluded."""
    lines = block.split("\n")
    if lines and PREVIEW_MARKER.match(lines[0]):
        lines = lines[1:]
    return [paragraph for paragraph
            in re.split(r"\n[ \t]*\n", "\n".join(lines).strip())
            if paragraph.strip()]


def _structure():
    """`bench/structure.py`, the comparer of the structure corpus (#20)."""
    bench = str(Path(__file__).resolve().parent.parent / "bench")
    if bench not in sys.path:
        sys.path.insert(0, bench)
    try:
        import structure
    except ImportError:
        raise CaseError(
            "bench/structure.py not found: page cases are compared with it, "
            "so they need the repository checkout") from None
    return structure


def compare(expected: str, replayed: str) -> list[str]:
    """Name the blocks in which a replay differs from the expected block.

    Empty when they match. Each block is described as `bench/structure.py`
    describes a reference block — text fingerprint, kind, heading level,
    footnote number and the footnote marks it carries — and the two
    sequences must be equal. That is `compare_structure` held block by
    block: a footnote mark that moved to another paragraph differs here.
    Bold, case and whitespace do not count.
    """
    structure = _structure()
    wanted = structure.reference_paragraphs(_paragraphs(expected))
    got = structure.reference_paragraphs(_paragraphs(replayed))

    def key(block):
        return json.dumps({name: value for name, value in block.items()
                           if name != "anchor"}, sort_keys=True)

    differing = []
    matcher = SequenceMatcher(None, [key(block) for block in wanted],
                              [key(block) for block in got], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        differing += [f"- expected block {index + 1}: {wanted[index]['anchor']}"
                      for index in range(i1, i2)]
        differing += [f"+ replayed block {index + 1}: {got[index]['anchor']}"
                      for index in range(j1, j2)]
    return differing


def uncorrected(case: dict) -> bool:
    """Whether the case was marked without a correction the comparer sees."""
    return not compare(case["expected"], case["produced"])


_FOOTNOTE_MARK = re.compile(r"\[\^\d+\]:?")
_WORD = re.compile(r"[^\W_]+")


def uncovered_words(case: dict, replayed: str) -> list[str]:
    """Words of the expected block the recognized lines do not hold.

    Any such word means the fault is upstream: no assembly can produce it.
    A word counts as held when it appears in the lines' text run together —
    which joins a word hyphenated across two lines — or in the replayed
    block, which holds what assembly itself adds (the callout title).
    """
    def words(text):
        return _WORD.findall(unicodedata.normalize("NFC", text).lower())

    def block_words(block):
        return words(_FOOTNOTE_MARK.sub(" ", "\n\n".join(_paragraphs(block))))

    run_together = "".join(words(
        " ".join(line.text
                 for line in page_cache.recognized_lines(case["page"]))))
    held = set(block_words(replayed))
    missing = []
    for word in block_words(case["expected"]):
        if word not in held and word not in run_together \
                and word not in missing:
            missing.append(word)
    return missing


class _Sources:
    """Running lines per source, computed once per run."""

    def __init__(self):
        self._lines: dict[tuple[str, str],
                          tuple[AssemblyContext | None, str]] = {}

    def context(self, case: dict) -> tuple[AssemblyContext, str]:
        """`(running lines, source state)` for a replay of `case`.

        Recomputed from the source with the current code, so a running-line
        fix reaches the case. The frozen copy stands in when the source is
        `missing` or no longer the file the lines came from (`stale`).
        """
        key = (case["pdf"], case["pdf_sha256"])
        if key not in self._lines:
            pdf = Path(case["pdf"])
            if not pdf.is_file():
                self._lines[key] = (None, "missing")
            elif page_cache.file_sha256(pdf) != case["pdf_sha256"]:
                self._lines[key] = (None, "stale")
            else:
                self._lines[key] = (source_assembly_context(pdf), "current")
        lines, state = self._lines[key]
        return (_frozen_context(case) if lines is None else lines, state)


# --- Run --------------------------------------------------------------------

@dataclass(frozen=True)
class Outcome:
    """What one case did in a run."""
    id: str
    path: Path
    # fixed | open | now matching | promoted | regressed | uncorrected |
    # upstream | unreadable | error
    state: str
    issue: int | None = None
    # current | missing | stale; None when the case was not replayed
    source: str | None = None
    differing: tuple[str, ...] = ()
    detail: str = ""

    @property
    def failed(self) -> bool:
        return self.state in ("regressed", "unreadable", "error")


def _walk(root: Path):
    """Every `.cases` folder below `root`; other hidden folders are skipped."""
    if root.name == CASES_DIR:
        yield root
        return
    for current, directories, _ in os.walk(root):
        if CASES_DIR in directories:
            yield Path(current) / CASES_DIR
        directories[:] = sorted(name for name in directories
                                if not name.startswith(".")
                                and name != "node_modules")


def drop_orphaned_stashes(cases: Path) -> int:
    """Remove the stashes of previews that were accepted or deleted.

    Either way the preview left its folder, and a stash is only there to
    survive a rerun of a preview still under review.
    """
    dropped = 0
    for stashes in cases.glob(f"*/{STASH_DIR}"):
        preview = cases.parent / f"{stashes.parent.name}.md"
        if not preview.exists():
            dropped += sum(1 for _ in stashes.glob("p*.json"))
            shutil.rmtree(stashes, ignore_errors=True)
    return dropped


def find_cases(roots) -> list[Path]:
    """Every case file below `roots`, once, whatever roots overlap."""
    return sorted({path.resolve() for root in roots
                   for cases in _walk(Path(root))
                   for path in cases.glob("*/p*.json")})


def run(roots, issue: int | None = None, promote: bool = False) -> list[Outcome]:
    """Replay every case below `roots`.

    An open case that still differs is a known failure. A fixed case that
    differs has regressed and fails the run. An open case that matches is
    reported, and becomes fixed only with `promote`. A case whose expected
    block equals its produced one was marked without a correction: it says
    nothing about a fix and is never promoted.
    """
    sources, outcomes = _Sources(), []
    for path in find_cases(roots):
        try:
            case = load_case(path)
        except CaseError as error:
            outcomes.append(Outcome(case_id(path), path, "unreadable",
                                    detail=str(error)))
            continue
        if issue is not None and case["issue"] != issue:
            continue

        def outcome(state, source=None, differing=(), detail=""):
            return Outcome(case_id(path), path, state, case["issue"], source,
                           tuple(differing), detail)

        if case["fault_stage"] == "upstream":
            outcomes.append(outcome("upstream"))
            continue
        try:
            if case["status"] != "fixed" and uncorrected(case):
                outcomes.append(outcome("uncorrected"))
                continue
            assembly, source = sources.context(case)
            differing = compare(case["expected"], replay(case, assembly))
        except CaseError:
            raise
        except Exception as error:  # a damaged case, or the code under repair
            outcomes.append(outcome("error", detail=f"replay failed: {error!r}"))
            continue
        if case["status"] == "fixed":
            state = "regressed" if differing else "fixed"
        elif differing:
            state = "open"
        elif promote:
            # The frozen running lines become the ones the case matched
            # with, so it still matches once its source is gone.
            fixed = {**case, "status": "fixed"}
            if source == "current":
                fixed.update(_frozen_lines(assembly))
            _write(path, fixed)
            state = "promoted"
        else:
            state = "now matching"
        outcomes.append(outcome(state, source, differing))
    return outcomes


# --- Command line -----------------------------------------------------------

_STASH_NOTES = {
    "stashed": "stashed",
    "kept": "stash kept",
    "held": "not stashed, the case holds the produced block",
}
_SOURCE_NOTES = {
    "missing": "source not found, frozen running lines",
    "stale": "stale: source changed, frozen running lines",
}
_SUMMARY = ("fixed", "promoted", "now matching", "open", "regressed",
            "uncorrected", "upstream", "unreadable", "error")
_STATE_NOTES = {
    "upstream": "(not replayed)",
    "uncorrected": "(expected equals produced: correct the page and mark "
                   "it again)",
}


def _print_run(outcomes, promote):
    for outcome in outcomes:
        parts = [f"{outcome.state:<13}{outcome.id}"]
        if outcome.issue is not None:
            parts.append(f"#{outcome.issue}")
        if outcome.state in _STATE_NOTES:
            parts.append(_STATE_NOTES[outcome.state])
        if outcome.source in _SOURCE_NOTES:
            parts.append(f"[{_SOURCE_NOTES[outcome.source]}]")
        if outcome.detail:
            parts.append(outcome.detail)
        print("  ".join(parts))
        for line in outcome.differing:
            print(f"    {line}")
    counts = {state: sum(outcome.state == state for outcome in outcomes)
              for state in _SUMMARY}
    print(f"\n{len(outcomes)} page case(s): " + ", ".join(
        f"{count} {state}" for state, count in counts.items() if count))
    if counts["now matching"] and not promote:
        print("now matching: run again with --promote to mark them fixed")


def case_line(path: Path, case: dict) -> str:
    """The line `add` and `list` print for a case. The plugin reads the page,
    the status and the fault stage from it (contracts/cli-contract.json)."""
    return (f"case {case_id(path)}: {case['status']}, "
            f"fault stage {case['fault_stage']}")


def _parser():
    parser = argparse.ArgumentParser(
        prog="pdf2md case",
        description="Page cases: keep a page the user marked as wrong and "
                    "replay it without the model.")
    commands = parser.add_subparsers(dest="command", required=True)

    stash_parser = commands.add_parser(
        "stash", help="keep the produced block of a page before it is edited")
    add_parser = commands.add_parser(
        "add", help="mark a page as wrong: its current block is the expected one")
    for sub in (stash_parser, add_parser):
        sub.add_argument("preview", type=Path)
        sub.add_argument("--page", type=int, required=True, metavar="N")
    add_parser.add_argument("--note", help="one line on what is wrong")
    add_parser.add_argument("--issue", type=int, metavar="N")
    add_parser.add_argument(
        "--fault-stage", choices=FAULT_STAGES,
        help="override the fault stage found by word coverage")

    list_parser = commands.add_parser(
        "list", help="name the pages of a preview that have a case")
    list_parser.add_argument("preview", type=Path)

    run_parser = commands.add_parser(
        "run", help="replay the cases below a folder")
    run_parser.add_argument(
        "roots", nargs="*", type=Path, metavar="FOLDER",
        help="vault or preview folder (default: $VAULT_ROOT)")
    run_parser.add_argument("--issue", type=int, metavar="N",
                            help="only the cases of this issue")
    run_parser.add_argument("--promote", action="store_true",
                            help="mark open cases that now match as fixed")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "stash":
            print(f"{_STASH_NOTES[stash(args.preview, args.page)]}: "
                  f"{case_id(case_path(args.preview, args.page))}")
            return 0
        if args.command == "add":
            path, case, missing = add(args.preview, args.page, args.note,
                                      args.issue, args.fault_stage)
            print(case_line(path, case))
            if missing:
                print("   not in the recognized lines: "
                      + ", ".join(missing[:8])
                      + (" …" if len(missing) > 8 else ""))
            if uncorrected(case):
                print("   the expected block equals the produced one — "
                      "correct the page and mark it again")
            return 0
        if args.command == "list":
            found, unreadable = list_cases(args.preview)
            for path, case in found:
                print(case_line(path, case))
            for reason in unreadable:
                print(f"pdf2md case: {reason}", file=sys.stderr)
            return 0

        roots = args.roots
        if not roots:
            vault = os.environ.get("VAULT_ROOT")
            if not vault:
                print("pdf2md case run: no folder given and VAULT_ROOT is "
                      "unset", file=sys.stderr)
                return 2
            roots = [Path(vault)]
        # `make check-cases VAULT_ROOT=~/vault` passes the tilde unexpanded.
        roots = [root.expanduser() for root in roots]
        for root in roots:
            if not root.is_dir():
                raise CaseError(f"not a folder: {root}")
        dropped = sum(drop_orphaned_stashes(cases)
                      for root in roots for cases in _walk(root))
        if dropped:
            print(f"removed {dropped} stash(es) of previews that were "
                  "accepted or deleted\n")
        outcomes = run(roots, args.issue, args.promote)
        if not outcomes:
            print("no page cases below "
                  + ", ".join(str(root) for root in roots)
                  + (f" for issue #{args.issue}" if args.issue else ""))
            return 0
        _print_run(outcomes, args.promote)
        return 1 if any(outcome.failed for outcome in outcomes) else 0
    except CaseError as error:
        print(f"pdf2md case: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

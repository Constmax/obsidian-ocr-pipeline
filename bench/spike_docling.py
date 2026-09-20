#!/usr/bin/env python3
"""Spike #95: measure Docling against the current PDF-to-Markdown pipeline.

Evaluation-only harness. It produces the same kind of per-page measurement
for both paths and scores both with ``bench/reading_order.score_page``
against the existing hand-checked truth set. No production code path is
touched: the ``pdf2md`` engine is driven through its CLI as a subprocess,
and the ``docling`` engine through ``bench/docling_adapter.py``.

Run one process per engine so the peak RSS belongs to that engine (same
pattern as ``bench/spike_rapidocr.py``)::

    python bench/spike_docling.py check
    python bench/spike_docling.py run --engine docling --pages pages/t01.pdf \\
        --out bench/docling-lauf --name docling-t01
    python bench/spike_docling.py run --engine pdf2md --pages pages/t01.pdf \\
        --out bench/docling-lauf --name pdf2md-t01
    python bench/spike_docling.py compare --docling bench/docling-lauf/docling-t01/result.json \\
        --pdf2md bench/docling-lauf/pdf2md-t01/result.json \\
        --truth-lines bench/reading-order-lauf/truth-lines

``run`` converts single-page PDFs (the ones ``bench/reading_order.py
prepare`` builds) so each output file is already one measured page: no
page-splitting of a multi-page Docling document is needed. ``compare``
reuses the truth-line records that ``bench/reading_order.py recognize``
writes; without them it reports timings only and marks ordering as missing.

All commands import without Docling, without the MLX model and without vault
assets, so the CI smoke check stays cheap. Heavy imports are function-local.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
REPOSITORY = BENCH.parent
PDF2MD_PY = REPOSITORY / "pdf2md" / "pdf2md.py"

T_PROCESS = time.monotonic()


def _rss_divisor() -> int:
    """Bytes per ``ru_maxrss`` unit on this platform.

    macOS reports bytes, Linux kilobytes. Split out for unit tests.
    """
    return 2 ** 20 if sys.platform == "darwin" else 1024


def peak_rss_mb() -> int:
    """Peak resident set size of this process in MiB."""
    import resource

    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
               / _rss_divisor())


def swap_state() -> dict:
    """macOS swap pressure before/after a run; empty when unavailable."""
    try:
        usage = subprocess.run(
            ["sysctl", "-n", "vm.swapusage"],
            capture_output=True, text=True, check=False).stdout.strip()
        vm = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, check=False).stdout
    except (OSError, FileNotFoundError):
        return {}
    stats: dict = {"swapusage": usage} if usage else {}
    for line in vm.splitlines():
        if line.startswith(("Swapins", "Swapouts", "Pageouts")):
            key, value = line.split(":")
            try:
                stats[key] = int(value.strip().rstrip("."))
            except ValueError:
                pass
    return stats


def extract_pdf2md_page_markdown(document: str) -> str:
    """Return the page body of a single-page ``pdf2md`` document.

    ``pdf2md`` writes frontmatter, a ``Quelle:`` link and one
    ``%% S. 1 | ... %%`` block. The scorer normalises away Markdown syntax,
    but frontmatter (title, source path) is still dropped so that only the
    converted page content is measured.
    """
    marker = "%% S."
    at = document.find(marker)
    if at < 0:
        return document
    end_of_marker_line = document.find("\n", at)
    if end_of_marker_line < 0:
        return ""
    return document[end_of_marker_line + 1:].strip() + "\n"


def summarize_timings(pages: list[dict]) -> dict:
    """Median/mean/min seconds per page over ``run`` page records."""
    seconds = [page["seconds"] for page in pages]
    if not seconds:
        return {"pages": 0, "median_s": None, "mean_s": None,
                "min_s": None, "total_s": 0.0}
    return {
        "pages": len(seconds),
        "median_s": statistics.median(seconds),
        "mean_s": statistics.fmean(seconds),
        "min_s": min(seconds),
        "total_s": sum(seconds),
    }


def _score_texts(texts_a: dict[str, str], texts_b: dict[str, str],
                 specs: list[dict], records: dict[str, dict]):
    """Score two page-text mappings with ``reading_order.score_page``.

    ``specs`` are truth specs from ``reading_order.load_truth`` and
    ``records`` maps page id to a ``recognize`` truth-line record
    (``{"width": ..., "height": ..., "lines": [...]}``). Returns
    ``(per_page, summaries)`` where ``per_page[engine][page_id]`` holds the
    ``score_page`` dict and ``summaries`` holds ``summarize`` per engine.
    Importing ``reading_order`` here keeps module import model-free.
    """
    sys.path.insert(0, str(BENCH))
    try:
        import reading_order as ro
    finally:
        try:
            sys.path.remove(str(BENCH))
        except ValueError:
            pass
    per_page: dict[str, dict] = {"docling": {}, "pdf2md": {}}
    for spec in specs:
        page_id = spec["id"]
        record = records.get(page_id)
        if record is None:
            continue
        truth, unassigned = ro.truth_lines(
            spec["regions"], record["lines"],
            record["width"], record["height"])
        if unassigned:
            raise SystemExit(
                f"{page_id}: {len(unassigned)} recognized lines lie outside "
                "every region; fix the truth and check the overlay again")
        for engine, texts in (("docling", texts_a), ("pdf2md", texts_b)):
            if page_id in texts:
                per_page[engine][page_id] = ro.score_page(
                    truth, texts[page_id])
    summaries = {engine: ro.summarize(pages)
                 for engine, pages in per_page.items() if pages}
    return per_page, summaries


def decision_guidance(summaries: dict, timings: dict) -> tuple[str, list[str]]:
    """Which branch of the issue #95 decision criteria the numbers point to.

    - ``migration candidate``: Docling is better on reading order (median
      accuracy strictly above the current path) **and** not slower per page.
    - ``keep current``: Docling is comparable or worse on either axis.
    - ``inconclusive``: ordering or timing is missing, or the medians tie.
      An inconclusive result is a valid spike outcome; the spike must not be
      extended to make Docling win.

    The written verdict lives in ``docs/docling-spike.md``; this helper only
    phrases what the measured numbers say so that ``compare`` output and the
    doc cannot drift apart.
    """
    docling = summaries.get("docling", {})
    current = summaries.get("pdf2md", {})
    if (docling.get("median") is None or current.get("median") is None
            or not timings.get("docling") or not timings.get("pdf2md")):
        return ("inconclusive",
                ["ordering or timing is missing for at least one path"])
    reasons = []
    better_order = docling["median"] > current["median"]
    not_slower = (timings["docling"].get("median_s") is not None
                  and timings["pdf2md"].get("median_s") is not None
                  and timings["docling"]["median_s"]
                  <= timings["pdf2md"]["median_s"])
    if docling["median"] == current["median"]:
        reasons.append(f"median ordering accuracy ties at "
                       f"{docling['median']:.1%}")
    elif better_order:
        reasons.append(f"median ordering accuracy {docling['median']:.1%} "
                       f"vs {current['median']:.1%} current")
    else:
        reasons.append(f"median ordering accuracy {docling['median']:.1%} "
                       f"vs {current['median']:.1%} current")
    doc_median = timings["docling"].get("median_s")
    cur_median = timings["pdf2md"].get("median_s")
    if doc_median is not None and cur_median is not None:
        reasons.append(f"median {doc_median:.1f} s/page vs "
                       f"{cur_median:.1f} s/page current")
    if better_order and not_slower:
        return ("migration candidate", reasons)
    if docling["median"] == current["median"]:
        return ("inconclusive", reasons)
    return ("keep current", reasons)


def format_comparison_table(per_page: dict, summaries: dict,
                            timings: dict) -> str:
    """Markdown table of per-page accuracy and per-engine timing."""

    def percent(value):
        return "–" if value is None else f"{value:.1%}"

    lines = ["| Page | Docling | Current (pdf2md) |",
             "|---|---|---|"]
    pages = sorted(set(per_page.get("docling", {}))
                   | set(per_page.get("pdf2md", {})))
    for page_id in pages:
        cells = []
        for engine in ("docling", "pdf2md"):
            page = per_page.get(engine, {}).get(page_id)
            cells.append(percent(page["accuracy"]) if page else "–")
        lines.append(f"| {page_id} | {cells[0]} | {cells[1]} |")
    lines.append("")
    lines.append("| Engine | Pages | Median accuracy | Median s/page | "
                 "Peak RSS (MiB) |")
    lines.append("|---|---|---|---|---|")
    for engine in ("docling", "pdf2md"):
        summary = summaries.get(engine, {})
        timing = timings.get(engine, {})
        median_s = timing.get("median_s")
        lines.append(
            f"| {engine} | {timing.get('pages', 0)} | "
            f"{percent(summary.get('median'))} | "
            f"{'–' if median_s is None else f'{median_s:.1f}'} | "
            f"{timing.get('peak_rss_mb', '–')} |")
    return "\n".join(lines) + "\n"


# ── Commands ───────────────────────────────────────────────────────────────


def check(_args) -> int:
    """Report whether Docling can run here (completion criterion 1)."""
    sys.path.insert(0, str(BENCH))
    try:
        import docling_adapter as adapter
    finally:
        try:
            sys.path.remove(str(BENCH))
        except ValueError:
            pass
    problems = adapter.missing_dependencies()
    print(f"docling pipeline : {adapter.PIPELINE} (documented local default)")
    print(f"docling version  : {adapter.docling_version() or 'not installed'}")
    if problems:
        print("status           : NOT RUNNABLE here")
        for problem in problems:
            print(f"  - {problem}")
        print("record this finding and close the spike with it "
              "(issue #95 allows that outcome).")
    else:
        print("status           : runnable (defaults, no tuning)")
    return 0


def _run_docling(pages: list[Path], run_dir: Path) -> list[dict]:
    sys.path.insert(0, str(BENCH))
    try:
        import docling_adapter as adapter
    finally:
        try:
            sys.path.remove(str(BENCH))
        except ValueError:
            pass
    problems = adapter.missing_dependencies()
    if problems:
        raise SystemExit("Docling is not ready:\n  " + "\n  ".join(problems))
    t_import_done = time.monotonic()
    records = adapter.convert_pages(pages)
    jsonable = adapter.records_to_jsonable(records)
    for record, pdf in zip(jsonable, pages):
        (run_dir / f"{Path(pdf).stem}.md").write_text(
            record["markdown"], encoding="utf-8")
        record.pop("markdown")
    print(f"docling import+init included in first page "
          f"({t_import_done - T_PROCESS:.1f} s cold-to-first-convert)")
    return jsonable


def _run_pdf2md(pages: list[Path], run_dir: Path, dpi: int) -> list[dict]:
    if not PDF2MD_PY.is_file():
        raise SystemExit(f"pdf2md entry point not found: {PDF2MD_PY}")
    jsonable = []
    for pdf in pages:
        page_id = Path(pdf).stem
        work = run_dir / "pdf2md-out" / page_id
        work.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, str(PDF2MD_PY), str(pdf),
             "--out", str(work), "--bild-dir", str(work / "assets"),
             "--dpi", str(dpi)],
            capture_output=True, text=True, check=False)
        seconds = time.perf_counter() - started
        if proc.returncode != 0:
            raise SystemExit(
                f"pdf2md failed for {pdf.name} "
                f"(exit {proc.returncode}):\n{proc.stderr[-2000:]}")
        outputs = sorted(work.glob("*.md"))
        if not outputs:
            raise SystemExit(f"pdf2md wrote no Markdown for {pdf.name}")
        markdown = extract_pdf2md_page_markdown(
            outputs[0].read_text(encoding="utf-8"))
        (run_dir / f"{page_id}.md").write_text(markdown, encoding="utf-8")
        jsonable.append({"page_id": page_id, "source_pdf": str(pdf),
                         "markdown_chars": len(markdown.strip()),
                         "seconds": seconds})
        print(f"{page_id} {seconds:.1f} s")
    return jsonable


def run(args) -> int:
    """Convert single-page PDFs with one engine and record the measurement."""
    pages = [Path(p) for p in args.pages]
    missing = [p for p in pages if not p.is_file()]
    if missing:
        raise SystemExit("not found: " + ", ".join(str(p) for p in missing))
    run_dir = Path(args.out) / args.name
    run_dir.mkdir(parents=True, exist_ok=True)
    swap_before = swap_state()
    started = time.monotonic()
    if args.engine == "docling":
        jsonable = _run_docling(pages, run_dir)
    elif args.engine == "pdf2md":
        jsonable = _run_pdf2md(pages, run_dir, args.dpi)
    else:
        raise SystemExit(f"unknown engine {args.engine}")
    elapsed = time.monotonic() - started
    result = {
        "engine": args.engine,
        "pages": jsonable,
        "timings": summarize_timings(jsonable),
        "peak_rss_mb": peak_rss_mb(),
        "wall_s": round(elapsed, 1),
        "swap_before": swap_before,
        "swap_after": swap_state(),
    }
    (run_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    timing = result["timings"]
    median = timing.get("median_s")
    print(f"{args.engine}: {len(jsonable)} pages, "
          f"{'–' if median is None else f'{median:.1f} s/page median, '}"
          f"peak {result['peak_rss_mb']} MiB")
    return 0


def _load_result(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare(args) -> int:
    """Score two ``run`` results against the truth set and print a table."""
    docling_result = _load_result(args.docling)
    pdf2md_result = _load_result(args.pdf2md)
    texts = {}
    for name, result in (("docling", docling_result),
                         ("pdf2md", pdf2md_result)):
        texts[name] = {}
        run_dir = Path(args.docling).parent if name == "docling" \
            else Path(args.pdf2md).parent
        for page in result.get("pages", []):
            page_id = page.get("page_id")
            if not page_id:
                continue
            md_file = run_dir / f"{page_id}.md"
            if md_file.is_file():
                texts[name][page_id] = md_file.read_text(encoding="utf-8")
    timings = {"docling": docling_result.get("timings", {}),
               "pdf2md": pdf2md_result.get("timings", {})}
    for name, result in (("docling", docling_result),
                         ("pdf2md", pdf2md_result)):
        timings[name]["peak_rss_mb"] = result.get("peak_rss_mb")
        timings[name]["pages"] = len(result.get("pages", []))

    per_page: dict = {"docling": {}, "pdf2md": {}}
    summaries: dict = {}
    if args.truth_lines is not None:
        sys.path.insert(0, str(BENCH))
        try:
            import reading_order as ro
        finally:
            try:
                sys.path.remove(str(BENCH))
            except ValueError:
                pass
        specs = ro.load_truth(args.truth)
        records = {}
        for spec in specs:
            path = Path(args.truth_lines) / f"{spec['id']}.json"
            if path.is_file():
                records[spec["id"]] = json.loads(
                    path.read_text(encoding="utf-8"))
        if records:
            per_page, summaries = _score_texts(
                texts["docling"], texts["pdf2md"], specs, records)

    table = format_comparison_table(per_page, summaries, timings)
    print(table)
    branch, reasons = decision_guidance(summaries, timings)
    print(f"Guidance (numbers only, verdict lives in docs/docling-spike.md): "
          f"**{branch}**")
    for reason in reasons:
        print(f"- {reason}")
    if args.out:
        out = Path(args.out)
        out.write_text(
            table + f"\nGuidance: **{branch}**\n"
            + "".join(f"- {reason}\n" for reason in reasons),
            encoding="utf-8")
        (out.with_suffix(".json")).write_text(json.dumps(
            {"per_page": {engine: {page: {k: v for k, v in scores.items()
                                          if k != "positions"}
                                   for page, scores in pages.items()}
                          for engine, pages in per_page.items()},
             "summaries": summaries, "timings": timings,
             "guidance": branch, "reasons": reasons},
            ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="report whether Docling can run here")
    run_parser = commands.add_parser(
        "run", help="convert single-page PDFs with one engine")
    run_parser.add_argument("--engine", required=True,
                            choices=("docling", "pdf2md"))
    run_parser.add_argument("--pages", nargs="+", required=True,
                            help="single-page PDFs (see reading_order prepare)")
    run_parser.add_argument("--out", default=str(BENCH / "docling-lauf"))
    run_parser.add_argument("--name", required=True)
    run_parser.add_argument("--dpi", type=int, default=150,
                            help="render dpi for the pdf2md engine only")
    compare_parser = commands.add_parser(
        "compare", help="score two run results against the truth set")
    compare_parser.add_argument("--docling", required=True,
                                help="docling run result.json")
    compare_parser.add_argument("--pdf2md", required=True,
                                help="pdf2md run result.json")
    compare_parser.add_argument("--truth", type=Path, default=BENCH
                                / "reading_order_truth.json")
    compare_parser.add_argument("--truth-lines", type=Path, default=None,
                                help="dir of <page>.json from "
                                     "'reading_order.py recognize'")
    compare_parser.add_argument("--out", type=Path, default=None,
                                help="write the comparison table to this .md")
    args = parser.parse_args(argv)
    if args.command == "check":
        return check(args)
    if args.command == "run":
        return run(args)
    return compare(args)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Stage-1 text-layer benchmark: retain or discard PaddleOCR (issue #71).

docs/paddle-textlayer.md, step 5. `bench_ocr.py` measures the Stage-2
Markdown path; this adapter measures what Stage 1 writes: the text layer of
the PDF that `reprocess-raw --output` publishes, read back with
`pdftotext -raw`, page by page. Run from the repository root with the Python
of the pinned OCRmyPDF environment (pypdfium2, img2pdf, Pillow):

  VAULT_ROOT=/path/to/vault python bench/stage1_bench.py select   # writes bench/stage1_cohorts.json
  VAULT_ROOT=/path/to/vault python bench/stage1_bench.py prepare
  python bench/stage1_bench.py run [WORKFLOW ...] [--repeat]
  python bench/stage1_bench.py score

Truth is the text layer of born-digital pages: each page is rendered at
300 dpi (grey) into an image-only PDF, runs through Stage 1, and is compared
with `pdftotext -raw` of the original page. Scanned pages have no such truth;
their reading order is measured by `reading_order.py` on the hand-checked
sets instead. Cohorts (bench/stage1_cohorts.json names every source page):

  words   the 40 vector pages of bench_ocr.py (bench/bench-lauf/herkunft.json),
          each marked one- or two-column from its text geometry
  short   short vector pages (1-300 characters: title, divider and closing
          slides) and graphical ones (many drawn paths, under 1500 characters)
  skew    ten of the `words` pages turned by +1.5, -2.5, +4, 90 and 180 degrees

Workflows are the real CLI, `reprocess-raw <cohort.pdf> --output <out.pdf>`
with `--min-chars 0`: the B5 gate is evaluated here per page instead, so one
short page does not discard a whole cohort. The page-count check stays on.
Peak memory is the largest resident set of any process of the run
(`/usr/bin/time -l`).

`score` prints, per cohort and workflow, word accuracy, word order and
citation fidelity as `bench_ocr.vergleiche()` computes them, pages failing
B5 (truth has 50 or more characters, the output fewer), seconds per page and
peak memory, then the paired comparison of each PaddleOCR workflow with the
best existing engine per metric. The decision margin is derived from the
page-to-page spread, as the plan asks: 1.96 standard errors of the best
existing engine's per-page scores on that cohort. Repeated runs (`--repeat`)
are compared page by page for stability.

Page images and outputs are course material and stay in bench/stage1-lauf/.
"""
import argparse
import hashlib
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

from paths import BENCH, REPOSITORY, VAULT_ROOT

COHORTS = BENCH / "stage1_cohorts.json"
RUN_DIR = BENCH / "stage1-lauf"
REPROCESS_RAW = REPOSITORY / "bin" / "reprocess-raw.sh"
PADDLE_SRC = REPOSITORY / "ocrmypdf_paddle" / "src"
DPI = 300
MIN_PAGE_CHARS = 50  # B5 default of reprocess-raw
SKEW_ANGLES = (1.5, -2.5, 4.0, 90.0, 180.0)

#: Existing engines as the plugin and the CLIs offer them, and the PaddleOCR
#: candidates. `tesseract` keeps its default policy (column-split retry when
#: the quality gate fails); `*-split` splits two-column pages up front.
WORKFLOWS = {
    "tesseract": ["--engine", "tesseract"],
    "tesseract-split": ["--engine", "tesseract", "--split-columns"],
    "apple": ["--engine", "apple"],
    "apple-split": ["--engine", "apple", "--split-columns"],
    "paddle-fast": ["--engine", "paddle", "--paddle-mode", "fast"],
    "paddle-fast-split": ["--engine", "paddle", "--paddle-mode", "fast", "--split-columns"],
    "paddle-accurate": ["--engine", "paddle", "--paddle-mode", "accurate"],
    "paddle-accurate-split": ["--engine", "paddle", "--paddle-mode", "accurate",
                              "--split-columns"],
}
EXISTING = ("tesseract", "tesseract-split", "apple", "apple-split")
CANDIDATES = tuple(name for name in WORKFLOWS if name.startswith("paddle"))
REPEATED = ("paddle-fast", "paddle-accurate")
METRICS = ("wortgenauigkeit", "reihenfolge", "zitattreue")


# ── select ──────────────────────────────────────────────────────────────────

def _page_facts(page):
    """Characters, image cover, drawn paths and a two-column guess of one page."""
    import pypdfium2.raw as raw

    width, height = page.get_size()
    textpage = page.get_textpage()
    chars = len("".join(textpage.get_text_range().split()))
    image_cover, paths = 0.0, 0
    for obj in page.get_objects():
        if obj.type == raw.FPDF_PAGEOBJ_IMAGE:
            left, bottom, right, top = obj.get_bounds()
            w = max(0.0, min(right, width) - max(left, 0.0))
            h = max(0.0, min(top, height) - max(bottom, 0.0))
            image_cover = max(image_cover, w * h / (width * height))
        elif obj.type == raw.FPDF_PAGEOBJ_PATH:
            paths += 1
    # Two columns: text on both halves, almost nothing across the middle.
    rects = [textpage.get_rect(i) for i in range(textpage.count_rects())]
    crossing = sum(1 for left, _, right, _ in rects if left < 0.47 * width and right > 0.53 * width)
    left_half = sum(1 for left, _, right, _ in rects if right <= 0.5 * width)
    right_half = sum(1 for left, _, right, _ in rects if left >= 0.5 * width)
    two_column = (len(rects) >= 20 and crossing <= 0.1 * len(rects)
                  and min(left_half, right_half) >= 0.25 * len(rects))
    return {"chars": chars, "image_cover": round(image_cover, 2), "paths": paths,
            "two_column": two_column}


def _one_per_file(rows, count):
    """The middle page of each file, then `count` files spread evenly."""
    by_file = {}
    for row in rows:
        by_file.setdefault(row["file"], []).append(row)
    picked = [group[len(group) // 2] for _, group in sorted(by_file.items())]
    step = max(1, len(picked) // count)
    return picked[::step][:count]


def select(args):
    """Writes bench/stage1_cohorts.json from the vault's raw/ PDFs."""
    import pypdfium2 as pdfium

    origin = json.loads((BENCH / "bench-lauf" / "herkunft.json").read_text(encoding="utf-8"))
    words = []
    for entry in origin:
        with_facts = {"file": entry["datei"], "page": entry["quellseite"]}
        doc = pdfium.PdfDocument(VAULT_ROOT / entry["datei"])
        with_facts["two_column"] = _page_facts(doc[entry["quellseite"] - 1])["two_column"]
        words.append(with_facts)

    used = {row["file"] for row in words}
    short, graphic = [], []
    for path in sorted((VAULT_ROOT / "raw").rglob("*.pdf")):
        relative = str(path.relative_to(VAULT_ROOT))
        if re.search(r"(^|/)_?assets/|archive", relative, re.I) or relative in used:
            continue
        try:
            doc = pdfium.PdfDocument(path)
        except pdfium.PdfiumError:
            continue
        for index in range(len(doc)):
            facts = _page_facts(doc[index])
            if facts["image_cover"] >= 0.5 or facts["chars"] == 0:
                continue  # a scan, or nothing to compare against
            row = {"file": relative, "page": index + 1}
            if facts["chars"] <= 300:
                short.append(row)
            elif facts["paths"] > 80 and facts["chars"] < 1500:
                graphic.append(row)
    short_pages = [dict(row, kind="short") for row in _one_per_file(short, args.short)]
    graphic_pages = [dict(row, kind="graphic") for row in _one_per_file(graphic, args.graphic)]

    skew = [dict(file=row["file"], page=row["page"], angle=SKEW_ANGLES[i % len(SKEW_ANGLES)])
            for i, row in enumerate(words[::4])]
    cohorts = {"words": words, "short": short_pages + graphic_pages, "skew": skew}
    COHORTS.write_text(json.dumps(cohorts, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
    print(f"{COHORTS}: words {len(words)} ({sum(r['two_column'] for r in words)} two-column), "
          f"short {len(short_pages)} + graphic {len(graphic_pages)} "
          f"(of {len(short)} / {len(graphic)}), skew {len(skew)}")


# ── prepare ─────────────────────────────────────────────────────────────────

def _truth_text(source, page):
    return subprocess.run(["pdftotext", "-raw", "-f", str(page), "-l", str(page), str(source),
                           "-"], capture_output=True, text=True, check=True).stdout


def prepare(args):
    """Image-only PDF per cohort plus the truth text of every page."""
    import img2pdf
    import pypdfium2 as pdfium
    from PIL import Image

    cohorts = json.loads(COHORTS.read_text(encoding="utf-8"))
    for name, rows in cohorts.items():
        images_dir = args.run_dir / "images" / name
        truth_dir = args.run_dir / "truth" / name
        images_dir.mkdir(parents=True, exist_ok=True)
        truth_dir.mkdir(parents=True, exist_ok=True)
        images = []
        for n, row in enumerate(rows, start=1):
            source = VAULT_ROOT / row["file"]
            (truth_dir / f"{n:02d}.txt").write_text(_truth_text(source, row["page"]),
                                                   encoding="utf-8")
            page = pdfium.PdfDocument(source)[row["page"] - 1]
            image = page.render(scale=DPI / 72, grayscale=True).to_pil().convert("L")
            angle = row.get("angle", 0.0)
            if angle in (90.0, 180.0):
                image = image.rotate(angle, expand=True)
            elif angle:
                image = image.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
            path = images_dir / f"{n:02d}.png"
            image.save(path, dpi=(DPI, DPI))
            images.append(str(path))
        target = args.run_dir / "cohorts" / f"{name}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(img2pdf.convert(images))
        print(f"{name}: {len(images)} pages → {target}")


# ── run ─────────────────────────────────────────────────────────────────────

def _env(run_dir):
    env = dict(os.environ)
    # The checkout's ocrmypdf_paddle, whatever an editable install points to.
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(PADDLE_SRC), env.get("PYTHONPATH")]))
    # reprocess-raw prefers $HOME/bin/pdf-combine, which may be another
    # checkout. An empty HOME makes it use this one; the venvs and models
    # that depend on HOME are named explicitly.
    home = Path.home()
    env.setdefault("VENV_ROOT", str(home / ".venvs"))
    env.setdefault("OCRMYPDF_PADDLE_MODEL_DIR", str(home / ".cache" / "ocrmypdf-paddle" / "models"))
    env["HOME"] = str(run_dir / "home")
    (run_dir / "home").mkdir(parents=True, exist_ok=True)
    return env


def run(args):
    cohorts = sorted((args.run_dir / "cohorts").glob("*.pdf"))
    names = args.workflows or list(WORKFLOWS)
    runs = [(name, "") for name in names]
    if args.repeat:
        runs = [(name, "@2") for name in names if name in REPEATED]
    for name, suffix in runs:
        out = args.run_dir / "runs" / f"{name}{suffix}"
        out.mkdir(parents=True, exist_ok=True)
        for cohort in cohorts:
            target = out / cohort.name
            if target.exists():
                continue
            log_path = out / f"{cohort.stem}.log"
            started = time.monotonic()
            with open(log_path, "w", encoding="utf-8") as log:
                result = subprocess.run(
                    ["/usr/bin/time", "-l", "bash", str(REPROCESS_RAW), str(cohort),
                     "--output", str(target), "--min-chars", "0", *WORKFLOWS[name]],
                    stdout=log, stderr=subprocess.STDOUT, env=_env(args.run_dir))
            seconds = time.monotonic() - started
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            rss = re.search(r"(\d+)\s+maximum resident set size", log_text)
            (out / f"{cohort.stem}.json").write_text(json.dumps({
                "exit": result.returncode, "seconds": round(seconds, 1),
                "peak_rss_mb": round(int(rss.group(1)) / 2**20) if rss else None,
            }) + "\n", encoding="utf-8")
            print(f"{name}{suffix} {cohort.stem}: exit {result.returncode}, {seconds:.0f} s",
                  flush=True)


# ── score ───────────────────────────────────────────────────────────────────

def _page_text(pdf, page):
    return subprocess.run(["pdftotext", "-raw", "-f", str(page), "-l", str(page), str(pdf), "-"],
                          capture_output=True, text=True, check=True).stdout


def _chars(text):
    return len("".join(text.split()))


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _percent(value):
    return "–" if value is None else f"{100 * value:.1f} %".replace(".", ",")


def _points(value):
    return "–" if value is None else f"{100 * value:+.1f}".replace(".", ",")


def _seconds(value):
    return f"{value:.1f}".replace(".", ",")


def _bootstrap(differences, rounds=10000, seed=71):
    """95 % percentile interval of the mean of paired differences."""
    rng = random.Random(seed)
    n = len(differences)
    means = sorted(sum(rng.choice(differences) for _ in range(n)) / n for _ in range(rounds))
    return means[int(0.025 * rounds)], means[int(0.975 * rounds) - 1]


def _margin(values):
    """1.96 standard errors of the per-page scores: what a cohort can resolve."""
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    return 1.96 * statistics.stdev(values) / math.sqrt(len(values))


def score_pages(args):
    """{run: {cohort: {"pages": [row | None], "meta": {...}}}} for every finished run."""
    from bench_ocr import vergleiche

    cohorts = json.loads(COHORTS.read_text(encoding="utf-8"))
    results = {}
    for out in sorted((args.run_dir / "runs").iterdir()):
        if not out.is_dir():
            continue
        for name, rows in cohorts.items():
            meta_path = out / f"{name}.json"
            if not meta_path.exists():
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            pdf = out / f"{name}.pdf"
            pages = []
            for n, row in enumerate(rows, start=1):
                truth = (args.run_dir / "truth" / name / f"{n:02d}.txt").read_text(
                    encoding="utf-8")
                text = _page_text(pdf, n) if pdf.exists() else ""
                scored = vergleiche(truth, text) or {}
                scored.update(chars=_chars(text), truth_chars=_chars(truth),
                              digest=hashlib.sha256(text.encode()).hexdigest(), row=row)
                scored["b5_failed"] = scored["truth_chars"] >= MIN_PAGE_CHARS > scored["chars"]
                pages.append(scored)
            results.setdefault(out.name, {})[name] = {"pages": pages, "meta": meta}
    return results


def _subsets(name, pages):
    """Cohort parts reported on their own lines."""
    if name == "words":
        return {"words": pages,
                "words, one column": [p for p in pages if not p["row"].get("two_column")],
                "words, two columns": [p for p in pages if p["row"].get("two_column")]}
    if name == "short":
        return {"short": [p for p in pages if p["row"].get("kind") == "short"],
                "graphic": [p for p in pages if p["row"].get("kind") == "graphic"]}
    if name == "skew":
        by_angle = {"skew": pages}
        for angle in SKEW_ANGLES:
            by_angle[f"skew {angle:+g}°"] = [p for p in pages if p["row"].get("angle") == angle]
        return by_angle
    return {name: pages}


def score(args):
    results = score_pages(args)
    cohorts = json.loads(COHORTS.read_text(encoding="utf-8"))
    base = {name: results[name] for name in WORKFLOWS if name in results}

    print("## Cohorts and workflows\n")
    print("| Cohort | Workflow | Pages | Word accuracy | Word order | Citations | "
          "B5 failed | Exit | Sec./page | Peak RSS |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for cohort in cohorts:
        for workflow, by_cohort in base.items():
            if cohort not in by_cohort:
                continue
            meta = by_cohort[cohort]["meta"]
            for label, pages in _subsets(cohort, by_cohort[cohort]["pages"]).items():
                if not pages:
                    continue
                print(f"| {label} | `{workflow}` | {len(pages)} | "
                      + " | ".join(_percent(_mean([p.get(m) for p in pages])) for m in METRICS)
                      + f" | {sum(p['b5_failed'] for p in pages)} | {meta['exit']} | "
                      f"{_seconds(meta['seconds'] / len(by_cohort[cohort]['pages']))} | "
                      f"{meta['peak_rss_mb']} MB |")

    print("\n## PaddleOCR against the best existing engine (paired per page)\n")
    print("| Cohort | Metric | Best existing | Its mean | Margin | Candidate | Difference "
          "| 95 % interval | New B5 failures |")
    print("|---|---|---|---:|---:|---|---:|---|---:|")
    for cohort in cohorts:
        existing = {n: base[n][cohort]["pages"] for n in EXISTING if cohort in base.get(n, {})}
        if not existing:
            continue
        for label in _subsets(cohort, next(iter(existing.values()))):
            for metric in ("wortgenauigkeit", "zitattreue"):
                pages_of = {n: _subsets(cohort, pages)[label] for n, pages in existing.items()}
                best = max(pages_of, key=lambda n: _mean([p.get(metric) for p in pages_of[n]])
                           or -1)
                best_pages = pages_of[best]
                margin = _margin([p.get(metric) for p in best_pages])
                for candidate in CANDIDATES:
                    if cohort not in base.get(candidate, {}):
                        continue
                    cand_pages = _subsets(cohort, base[candidate][cohort]["pages"])[label]
                    diffs = [c.get(metric) - b.get(metric) for c, b in zip(cand_pages, best_pages)
                             if c.get(metric) is not None and b.get(metric) is not None]
                    if not diffs:
                        continue
                    low, high = _bootstrap(diffs)
                    new_b5 = sum(1 for c, b in zip(cand_pages, best_pages)
                                 if c["b5_failed"] and not b["b5_failed"])
                    print(f"| {label} | {metric} | `{best}` | "
                          f"{_percent(_mean([p.get(metric) for p in best_pages]))} | "
                          f"{_points(margin)} | `{candidate}` | {_points(_mean(diffs))} | "
                          f"{_points(low)} … {_points(high)} | {new_b5} |")

    repeats = [name for name in results if name.endswith("@2")]
    if repeats:
        print("\n## Repeated runs\n")
        print("| Workflow | Cohort | Pages with identical text |")
        print("|---|---|---:|")
        for name in repeats:
            first = results.get(name[:-2], {})
            for cohort, data in results[name].items():
                if cohort not in first:
                    continue
                same = sum(a["digest"] == b["digest"]
                           for a, b in zip(data["pages"], first[cohort]["pages"]))
                print(f"| `{name[:-2]}` | {cohort} | {same} of {len(data['pages'])} |")

    if args.json:
        args.json.write_text(json.dumps(
            {run: {c: {"meta": d["meta"], "pages": [{k: v for k, v in p.items() if k != "row"}
                                                    for p in d["pages"]]}
                   for c, d in by_cohort.items()} for run, by_cohort in results.items()},
            ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    commands = parser.add_subparsers(dest="command", required=True)
    select_parser = commands.add_parser("select")
    select_parser.add_argument("--short", type=int, default=12)
    select_parser.add_argument("--graphic", type=int, default=8)
    commands.add_parser("prepare")
    run_parser = commands.add_parser("run")
    run_parser.add_argument("workflows", nargs="*", metavar="WORKFLOW",
                            help=f"default: all of {', '.join(WORKFLOWS)}")
    run_parser.add_argument("--repeat", action="store_true",
                            help=f"second run of {', '.join(REPEATED)} for the stability check")
    score_parser = commands.add_parser("score")
    score_parser.add_argument("--json", type=Path, help="also write every page score here")
    args = parser.parse_args(argv)
    {"select": select, "prepare": prepare, "run": run, "score": score}[args.command](args)


if __name__ == "__main__":
    main()

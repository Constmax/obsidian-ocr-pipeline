#!/usr/bin/env python3
"""Reading order of the Stage-1 text layer, measured on a hand-checked truth set.

Issue #69, docs/paddle-textlayer.md step 3. Run from the repository root, in
this order:

  VAULT_ROOT=/path/to/vault python bench/reading_order.py prepare
  python bench/reading_order.py recognize   # RapidOCR and the pinned models
  python bench/reading_order.py overlay     # images for the hand check
  python bench/reading_order.py run         # every workflow on every page
  python bench/reading_order.py score       # comparison and gate decision

All commands need the pinned OCRmyPDF environment (img2pdf, pikepdf).
`recognize` and the Paddle workflows also need rapidocr, onnxruntime and
OCRMYPDF_PADDLE_MODEL_DIR (docs/paddle-textlayer.md, step 2); the Apple and
Tesseract workflows run bin/pdf-auto.sh and need `ocrmypdf` on PATH.

The truth (bench/reading_order_truth.json) names each source page and lists
hand-drawn regions in reading order: header, column body, footnotes, margin
notes, full-width headings and footers. It holds no page text. The truth
lines are the PP-OCRv5 lines recognized on the page image, which the pinned
models reproduce exactly. Each line belongs to the smallest region containing
its centre and is read in rows within that region. Page images, recognized
text, overlays and outputs are copyrighted course material and stay in
bench/reading-order-lauf/.

Scoring, as the plan specifies: a truth line is matched when at least half
of its 4-grams (lower-case letters, digits and § only) occur in the
workflow's `pdftotext -raw` text at one consistent offset. Ordering accuracy
is the share of matched line pairs whose extracted order agrees with the
truth. Lines with fewer than 10 such characters are too ambiguous to place
and are left out; unmatched lines and lines found more often than the truth
holds them (each further copy with at least 80 % of the 4-grams) are
reported separately.
"""
import argparse
import itertools
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from paths import BENCH, REPOSITORY, VAULT_ROOT

TRUTH = BENCH / "reading_order_truth.json"
RUN_DIR = BENCH / "reading-order-lauf"
PADDLE_SRC = REPOSITORY / "ocrmypdf_paddle" / "src"
COLUMN_TOOLS = REPOSITORY / "bin" / "column_tools.py"
PDF_AUTO = REPOSITORY / "bin" / "pdf-auto.sh"

GRAM = 4
MIN_CHARS = 10
MIN_COVERAGE = 0.5
#: A second copy counts as a duplicate only when it is nearly complete;
#: lines sharing a phrase ("eine lange erste/zweite Zeile") reach half.
DUPLICATE_COVERAGE = 0.8
MIN_PAGE_CHARS = 50  # B5 default of reprocess-raw
GATE_POINTS = 0.01

ROLES = {"header", "heading", "body", "footnote", "note", "footer"}
COLUMN_ROLES = {"body", "footnote", "note"}

#: Stage-1 workflows as bin/pdf-auto.sh runs them today. The split runs keep
#: the quality gate and its retries, as the current workflow does; the
#: unsplit runs are plain OCR of the whole page.
SHELL_WORKFLOWS = {
    "split-apple": ["--engine", "apple", "--split-columns"],
    "split-tesseract": ["--engine", "tesseract", "--split-columns"],
    "unsplit-apple": ["--engine", "apple", "--no-quality-gate"],
    "unsplit-tesseract": ["--engine", "tesseract", "--no-quality-gate"],
}
#: PaddleOCR through the plugin, split (the supported command) or unsplit.
#: Arguments mirror build_ocr_args in bin/pdf-lib.sh for one job.
PADDLE_WORKFLOWS = {"split-paddle": True, "unsplit-paddle": False}
WORKFLOWS = [*SHELL_WORKFLOWS, *PADDLE_WORKFLOWS]
SPLIT_BASELINES = ("split-apple", "split-tesseract")
CANDIDATE = "unsplit-paddle"
#: Line orders computed from the truth lines without OCRmyPDF, for reference.
LINE_ORDERS = ("rapidocr-order", "order_lines")


# ── Truth ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TruthLine:
    order: int
    text: str
    key: str
    role: str
    column: str | None
    section: int
    full_width: bool
    polygon: tuple


def load_truth(path=TRUTH):
    pages = json.loads(Path(path).read_text(encoding="utf-8"))["pages"]
    for page in pages:
        two_column = any(region.get("column") for region in page["regions"])
        for region in page["regions"]:
            role, column = region["role"], region.get("column")
            if role not in ROLES:
                raise ValueError(f"{page['id']}: unknown role {role!r}")
            if column not in (None, "L", "R"):
                raise ValueError(f"{page['id']}: column must be L or R, not {column!r}")
            if two_column and (role in COLUMN_ROLES) != bool(column):
                raise ValueError(
                    f"{page['id']}: on a two-column page {role} regions "
                    f"{'need' if role in COLUMN_ROLES else 'take no'} column")
    return pages


def normalize(text):
    """Lower-case letters, digits and § only: what every engine can agree on."""
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(c for c in text if c.isalnum() or c == "§")


def _midpoint(points):
    return (sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points))


def _thickness(polygon):
    """Line height of a quadrilateral: its area over its length."""
    by_x = sorted(polygon)
    (ax, ay), (bx, by) = _midpoint(by_x[:2]), _midpoint(by_x[2:])
    length = math.hypot(bx - ax, by - ay) or 1.0
    cx, cy = _midpoint(polygon)
    ring = sorted(polygon, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    area = abs(sum(x0 * y1 - x1 * y0
                   for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]))) / 2
    return area / length


def read_rows(lines):
    """Lines of one region in rows, top to bottom, each row left to right.

    A row is keyed by the height of the line's left end, so long lines on a
    skewed page do not swallow their neighbours.
    """
    measured = []
    for line in lines:
        points = sorted(tuple(p) for p in line["polygon"])
        left_y = (points[0][1] + points[1][1]) / 2
        measured.append((left_y, points[0][0], _thickness(points), line))
    measured.sort(key=lambda m: (m[0], m[1]))
    rows = []
    for m in measured:
        if rows and abs(m[0] - rows[-1][0][0]) <= 0.4 * min(m[2], rows[-1][0][2]):
            rows[-1].append(m)
        else:
            rows.append([m])
    return [m[3] for row in rows for m in sorted(row, key=lambda m: m[1])]


def truth_lines(regions, lines, width, height):
    """(truth lines in reading order, indices of lines outside every region)."""
    two_column = any(region.get("column") for region in regions)
    members = defaultdict(list)
    unassigned = []
    for i, line in enumerate(lines):
        if len(line["polygon"]) != 4 or not line["text"].strip():
            continue
        cx, cy = _midpoint([tuple(p) for p in line["polygon"]])
        cx, cy = cx / width * 1000, cy / height * 1000
        containing = [k for k, region in enumerate(regions)
                      if region["box"][0] <= cx <= region["box"][2]
                      and region["box"][1] <= cy <= region["box"][3]]
        if not containing:
            unassigned.append(i)
            continue
        smallest = min(containing, key=lambda k: (
            (regions[k]["box"][2] - regions[k]["box"][0])
            * (regions[k]["box"][3] - regions[k]["box"][1]), k))
        members[smallest].append(line)
    truth = []
    for k, region in enumerate(regions):
        column = region.get("column")
        section = sum(1 for earlier in regions[:k] if not earlier.get("column"))
        for line in read_rows(members[k]):
            truth.append(TruthLine(len(truth), line["text"], normalize(line["text"]),
                                   region["role"], column, section,
                                   two_column and not column,
                                   tuple(tuple(p) for p in line["polygon"])))
    return truth, unassigned


# ── Matching and metrics ───────────────────────────────────────────────────


def gram_index(text, k=GRAM):
    index = defaultdict(list)
    for i in range(len(text) - k + 1):
        index[text[i:i + k]].append(i)
    return index


def occurrences(needle, index, k=GRAM, min_coverage=MIN_COVERAGE):
    """[(start, coverage)] of approximate occurrences of `needle`, best first.

    Every shared k-gram votes for an offset; a window of offsets within a
    tenth of the needle's length counts the distinct needle k-grams it holds.
    Occurrences closer than half the needle's length are one occurrence.
    """
    total = len(needle) - k + 1
    if total < 1:
        return []
    hits = sorted((p - i, i) for i in range(total) for p in index.get(needle[i:i + k], ()))
    slack = max(3, len(needle) // 10)
    windows, counts, end = [], Counter(), 0
    for start in range(len(hits)):
        while end < len(hits) and hits[end][0] - hits[start][0] <= slack:
            counts[hits[end][1]] += 1
            end += 1
        windows.append((len(counts) / total, hits[(start + end - 1) // 2][0]))
        counts[hits[start][1]] -= 1
        if not counts[hits[start][1]]:
            del counts[hits[start][1]]
    windows.sort(key=lambda w: (-w[0], w[1]))
    found = []
    for coverage, offset in windows:
        if coverage < min_coverage:
            break
        if all(abs(offset - other) > len(needle) // 2 for other, _ in found):
            found.append((offset, coverage))
    return found


def precedence(positions, orders=None):
    """Share of line pairs whose extracted order agrees with the truth order."""
    matched = sorted(positions if orders is None else set(orders) & set(positions))
    pairs = list(itertools.combinations(matched, 2))
    if not pairs:
        return None
    return sum(positions[a] < positions[b] for a, b in pairs) / len(pairs)


def score_page(truth, extracted):
    haystack = normalize(extracted)
    index = gram_index(haystack)
    eligible = [line for line in truth if len(line.key) >= MIN_CHARS]
    groups = defaultdict(list)
    for line in eligible:
        groups[line.key].append(line)
    positions, duplicated, unmatched = {}, [], []
    # Longer lines claim their text first: a short line that also occurs inside
    # a longer line (a citation repeated in a footnote) must not take its place.
    claimed = []
    for key, members in sorted(groups.items(), key=lambda item: -len(item[0])):
        found = [(offset, coverage) for offset, coverage in occurrences(key, index)
                 if all(min(offset + len(key), end) - max(offset, start) <= len(key) // 2
                        for start, end in claimed)]
        if sum(coverage >= DUPLICATE_COVERAGE for _, coverage in found) > len(members):
            duplicated.extend(members)
        used = sorted(found[:len(members)])
        for line, (offset, _) in zip(members, used):
            positions[line.order] = offset
            claimed.append((offset, offset + len(key)))
        unmatched.extend(members[len(used):])

    by_order = {line.order: line for line in truth}
    sections = defaultdict(list)
    for order, offset in positions.items():
        line = by_order[order]
        if line.column and line.role in ("body", "footnote"):
            sections[line.section].append((offset, line.column))
    interleaved = 0
    for items in sections.values():
        labels = [column for _, column in sorted(items)]
        runs = 1 + sum(a != b for a, b in zip(labels, labels[1:]))
        if len(set(labels)) == 2 and (runs > 2 or labels[0] == "R"):
            interleaved += 1

    # A full-width line is misplaced when it swaps places with a column line;
    # the order among header lines is no column assignment.
    full = [line for line in eligible if line.full_width]
    column_positions = [(order, offset) for order, offset in positions.items()
                        if by_order[order].column]
    misplaced = [
        line for line in full if line.order in positions and any(
            (line.order < other) != (positions[line.order] < offset)
            for other, offset in column_positions)
    ]
    return {
        "lines": len(truth),
        "eligible": len(eligible),
        "matched": len(positions),
        "unmatched": len(unmatched),
        "duplicated": len(duplicated),
        "accuracy": precedence(positions),
        "interleaved_sections": interleaved,
        "full_width": len(full),
        "full_width_unmatched": sum(line.full_width for line in unmatched),
        "full_width_duplicated": sum(line.full_width for line in duplicated),
        "full_width_misplaced": len(misplaced),
        "positions": positions,
    }


def summarize(pages):
    """Aggregate per-page scores of one workflow."""
    accuracies = [p["accuracy"] for p in pages.values() if p["accuracy"] is not None]
    common = [p["common_accuracy"] for p in pages.values()
              if p.get("common_accuracy") is not None]
    total = lambda key: sum(p[key] for p in pages.values())
    return {
        "pages": len(pages),
        "median": statistics.median(accuracies) if accuracies else None,
        "mean": statistics.fmean(accuracies) if accuracies else None,
        "min": min(accuracies) if accuracies else None,
        "median_common": statistics.median(common) if common else None,
        "interleaved_pages": sum(p["interleaved_sections"] > 0 for p in pages.values()),
        "full_width": total("full_width"),
        "full_width_misplaced": total("full_width_misplaced"),
        "full_width_duplicated": total("full_width_duplicated"),
        "full_width_unmatched": total("full_width_unmatched"),
        "eligible": total("eligible"),
        "unmatched": total("unmatched"),
        "duplicated": total("duplicated"),
    }


def decide(summaries, checks):
    """("unsplit is supported" | "keep split mode", reasons) per the plan's gate."""
    candidate = summaries.get(CANDIDATE)
    baselines = [summaries[name]["median"] for name in SPLIT_BASELINES
                 if name in summaries and summaries[name]["median"] is not None]
    if candidate is None or candidate["median"] is None or not baselines:
        return "keep split mode", ["the candidate or every split baseline is missing"]
    reasons = []
    baseline = max(baselines)
    if candidate["full_width_misplaced"] or candidate["full_width_duplicated"]:
        reasons.append(
            f"{candidate['full_width_misplaced']} full-width lines misplaced, "
            f"{candidate['full_width_duplicated']} duplicated")
    if candidate["full_width_unmatched"]:
        reasons.append(f"{candidate['full_width_unmatched']} full-width lines not found")
    if candidate["interleaved_pages"]:
        reasons.append(f"columns interleaved on {candidate['interleaved_pages']} pages")
    if candidate["median"] < baseline - GATE_POINTS:
        reasons.append(f"median ordering accuracy {candidate['median']:.1%} is more than one "
                       f"point below the split baseline {baseline:.1%}")
    failed = [f"{page}: {', '.join(problems)}"
              for page, problems in checks.get(CANDIDATE, {}).items() if problems]
    if failed:
        reasons.append("page checks failed (" + "; ".join(failed) + ")")
    return ("keep split mode" if reasons else "unsplit is supported"), reasons


# ── Commands ───────────────────────────────────────────────────────────────


def _pages_dir(args):
    return args.run_dir / "pages"


def prepare(args):
    """Page images (300 dpi, grey, A4 height) and image-only PDFs of them."""
    import img2pdf

    target = _pages_dir(args)
    target.mkdir(parents=True, exist_ok=True)
    for spec in load_truth():
        png, pdf = target / f"{spec['id']}.png", target / f"{spec['id']}.pdf"
        if not png.exists():
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run(["pdftoppm", "-gray", "-png", "-scale-to", "3508",
                                "-f", str(spec["page"]), "-l", str(spec["page"]), "-singlefile",
                                str(VAULT_ROOT / spec["source"]), f"{tmp}/page"], check=True)
                shutil.move(f"{tmp}/page.png", png)
        if not pdf.exists():
            pdf.write_bytes(img2pdf.convert(
                str(png), layout_fun=img2pdf.get_fixed_dpi_layout_fun((300, 300))))
        print(spec["id"], "ok")


def recognize(args):
    """PP-OCRv5 lines of every page image: the text of the truth lines."""
    sys.path.insert(0, str(PADDLE_SRC))
    from dataclasses import asdict

    from ocrmypdf_paddle import runtime

    problems = runtime.missing_runtime() + runtime.check_models(runtime.model_dir())
    if problems:
        raise SystemExit("PaddleOCR is not ready:\n  " + "\n  ".join(problems))
    recognizer = runtime.Recognizer()
    target = args.run_dir / "truth-lines"
    target.mkdir(parents=True, exist_ok=True)
    for spec in load_truth():
        started = time.monotonic()
        page = recognizer.recognize(_pages_dir(args) / f"{spec['id']}.png")
        record = {"width": page.width, "height": page.height,
                  "lines": [asdict(line) for line in page.lines]}
        (target / f"{spec['id']}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{spec['id']} {len(page.lines)} lines {time.monotonic() - started:.1f} s")


def _recognized(args, page_id):
    path = args.run_dir / "truth-lines" / f"{page_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


COLORS = {"header": (120, 120, 120), "heading": (200, 0, 160), "body": (0, 90, 220),
          "footnote": (0, 150, 150), "note": (230, 120, 0), "footer": (120, 120, 120)}


def overlay(args):
    """Regions and numbered truth lines on the page, in two halves per page."""
    from PIL import Image, ImageDraw

    target = args.run_dir / "overlay"
    target.mkdir(parents=True, exist_ok=True)
    only = set(args.pages or [])
    for spec in load_truth():
        if only and spec["id"] not in only:
            continue
        record = _recognized(args, spec["id"])
        width, height = record["width"], record["height"]
        truth, unassigned = truth_lines(spec["regions"], record["lines"], width, height)
        image = Image.open(_pages_dir(args) / f"{spec['id']}.png").convert("RGB")
        draw = ImageDraw.Draw(image)
        # Grid in thousandths, for drawing region boxes by eye.
        for step in range(0, 1001, 25):
            shade = (150, 200, 150) if step % 100 == 0 else (215, 235, 215)
            x, y = step * width / 1000, step * height / 1000
            draw.line((x, 0, x, height), fill=shade, width=3 if step % 100 == 0 else 1)
            draw.line((0, y, width, y), fill=shade, width=3 if step % 100 == 0 else 1)
            if step % 100 == 0:
                draw.text((x + 6, 6), str(step), fill=(0, 120, 0))
                draw.text((6, y + 6), str(step), fill=(0, 120, 0))
        for k, region in enumerate(spec["regions"]):
            x0, y0, x1, y1 = region["box"]
            box = (x0 * width / 1000, y0 * height / 1000, x1 * width / 1000, y1 * height / 1000)
            color = COLORS[region["role"]]
            draw.rectangle(box, outline=color, width=6)
            draw.text((box[0] + 8, box[1] + 4),
                      f"R{k} {region['role']} {region.get('column') or ''}", fill=color)
        for line in truth:
            color = COLORS[line.role]
            draw.polygon(line.polygon, outline=color)
            x, y = min(line.polygon)
            draw.rectangle((x - 70, y, x - 4, y + 26), fill=(255, 255, 255))
            draw.text((x - 68, y + 2), str(line.order), fill=(220, 0, 0))
        for i in unassigned:
            draw.polygon([tuple(p) for p in record["lines"][i]["polygon"]],
                         outline=(255, 0, 0), width=5)
        scaled = image.resize((width // 2, height // 2))
        scaled.crop((0, 0, width // 2, height // 4 + 40)).save(target / f"{spec['id']}-top.png")
        scaled.crop((0, height // 4 - 40, width // 2, height // 2)).save(
            target / f"{spec['id']}-bottom.png")
        print(f"{spec['id']}: {len(truth)} truth lines, {len(unassigned)} outside every region")


def _log(args, name):
    return open(args.run_dir / "runs" / f"{name}.log", "a", encoding="utf-8")


def run(args):
    specs = load_truth()
    pages = _pages_dir(args)
    for name in args.workflows or WORKFLOWS:
        out = args.run_dir / "runs" / name
        out.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        if name in SHELL_WORKFLOWS:
            with _log(args, name) as log:
                subprocess.run([str(PDF_AUTO), str(pages), "--output-dir", str(out),
                                *SHELL_WORKFLOWS[name]], stdout=log, stderr=subprocess.STDOUT)
        elif name in PADDLE_WORKFLOWS:
            for spec in specs:
                _run_paddle(args, name, PADDLE_WORKFLOWS[name], pages / f"{spec['id']}.pdf", out)
        else:
            raise SystemExit(f"unknown workflow {name}")
        print(f"{name}: {time.monotonic() - started:.0f} s")


def _run_paddle(args, name, split, source, out):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(PADDLE_SRC), env.get("PYTHONPATH")]))
    env["OCRMYPDF_PADDLE_DEBUG_DIR"] = str(out / "debug" / source.stem)
    work = out / "work"
    work.mkdir(exist_ok=True)
    ocr = [args.python, "-m", "ocrmypdf", "--plugin", "ocrmypdf_paddle", "-l", "deu",
           "--skip-text", "--optimize", str(args.optimize), "--jobs", "1",
           "--max-image-mpixels", "400"]
    target = out / source.name
    with _log(args, name) as log:
        log.write(f"== {source.name}\n")
        log.flush()
        call = lambda command: subprocess.run([str(c) for c in command], env=env, stdout=log,
                                              stderr=subprocess.STDOUT, check=True)
        started = time.monotonic()
        if split:
            halves, page_map, recognized = (work / f"{source.stem}-{suffix}"
                                            for suffix in ("split.pdf", "map.json", "ocr.pdf"))
            call([args.python, COLUMN_TOOLS, "split", source, halves, "--map", page_map, "--auto"])
            call([*ocr, halves, recognized])
            call([args.python, COLUMN_TOOLS, "merge", recognized, target, "--map", page_map])
        else:
            call([*ocr, "--rotate-pages", "--deskew", source, target])
        log.write(f"seconds {time.monotonic() - started:.1f}\n")


def _pdftotext(pdf):
    return subprocess.run(["pdftotext", "-raw", str(pdf), "-"], capture_output=True, text=True,
                          check=True).stdout


def page_checks(pdf, python):
    """Problems with one output page: page count and B5 text coverage."""
    problems = []
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    pages = re.search(r"^Pages:\s+(\d+)", info, re.M)
    if not pages or pages.group(1) != "1":
        problems.append(f"{pages.group(1) if pages else 'no'} pages instead of 1")
    verify = subprocess.run([python, str(COLUMN_TOOLS), "verify-pages", str(pdf),
                             "--min-chars", str(MIN_PAGE_CHARS)], capture_output=True, text=True)
    if verify.returncode:
        problems.append(f"B5: fewer than {MIN_PAGE_CHARS} characters")
    return problems


def _line_orders(record):
    lines = record["lines"]
    orders = {"rapidocr-order": "\n".join(line["text"] for line in lines)}
    try:
        sys.path.insert(0, str(PADDLE_SRC))
        from ocrmypdf_paddle.hocr import TextLine
        from ocrmypdf_paddle.ordering import order_lines
    except ImportError:
        return orders
    text_lines = [TextLine(line["text"], tuple(tuple(p) for p in line["polygon"]),
                           line["confidence"]) for line in lines]
    ordered = order_lines(text_lines, record["width"], record["height"])
    orders["order_lines"] = "\n".join(line.text for line in ordered)
    return orders


def _percent(value):
    return "–" if value is None else f"{value:.1%}"


def score(args):
    specs = load_truth()
    names = [name for name in WORKFLOWS if (args.run_dir / "runs" / name).is_dir()]
    results = defaultdict(dict)
    checks = defaultdict(dict)
    sizes = defaultdict(dict)
    for spec in specs:
        page_id = spec["id"]
        record = _recognized(args, page_id)
        truth, unassigned = truth_lines(spec["regions"], record["lines"], record["width"],
                                        record["height"])
        if unassigned:
            raise SystemExit(f"{page_id}: {len(unassigned)} recognized lines lie outside "
                             "every region; fix the truth and check the overlay again")
        texts = {}
        for name in names:
            pdf = args.run_dir / "runs" / name / f"{page_id}.pdf"
            if pdf.exists():
                texts[name] = _pdftotext(pdf)
                checks[name][page_id] = page_checks(pdf, args.python)
                sizes[name][page_id] = pdf.stat().st_size
            else:
                checks[name][page_id] = ["no output"]
        texts.update(_line_orders(record))
        for name, text in texts.items():
            results[name][page_id] = score_page(truth, text)
        common = set.intersection(*(set(results[name][page_id]["positions"])
                                    for name in texts))
        for name in texts:
            page = results[name][page_id]
            page["common_accuracy"] = precedence(page["positions"], common)

    summaries = {name: summarize(pages) for name, pages in results.items()}
    decision, reasons = decide(summaries, checks)

    print("| Workflow | Pages | Median | Mean | Min | Median (common lines) | Interleaved "
          "pages | Full-width misplaced / duplicated / not found | Unmatched | Duplicated | "
          "Page checks failed | Output size |")
    print("|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|")
    for name, s in summaries.items():
        failed = sum(bool(p) for p in checks.get(name, {}).values())
        size = sum(sizes[name].values()) / 1024 ** 2 if sizes.get(name) else None
        print(f"| `{name}` | {s['pages']} | {_percent(s['median'])} | {_percent(s['mean'])} | "
              f"{_percent(s['min'])} | {_percent(s['median_common'])} | "
              f"{s['interleaved_pages']} | {s['full_width_misplaced']} / "
              f"{s['full_width_duplicated']} / {s['full_width_unmatched']} of {s['full_width']} | "
              f"{s['unmatched']} of {s['eligible']} | {s['duplicated']} | "
              f"{failed if name in checks else '–'} | "
              f"{'–' if size is None else f'{size:.1f} MB'} |")
    print()
    print("| Page | Layout | " + " | ".join(f"`{name}`" for name in results) + " |")
    print("|---|---|" + "---:|" * len(results))
    for spec in specs:
        cells = []
        for name in results:
            page = results[name].get(spec["id"])
            if page is None:
                cells.append("–")
                continue
            mark = "*" if page["interleaved_sections"] else ""
            cells.append(f"{_percent(page['accuracy'])}{mark}")
        print(f"| {spec['id']} | {spec['layout']} | " + " | ".join(cells) + " |")
    print()
    print(f"Decision: **{decision}**")
    for reason in reasons:
        print(f"- {reason}")

    report = {
        "decision": decision, "reasons": reasons, "summaries": summaries,
        "checks": checks, "sizes": sizes,
        "pages": {name: {page: {k: v for k, v in scores.items() if k != "positions"}
                         for page, scores in pages.items()}
                  for name, pages in results.items()},
    }
    (args.run_dir / "score.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run-dir", type=Path, default=RUN_DIR)
    parser.add_argument("--python", default=sys.executable,
                        help="Python of the OCRmyPDF environment (default: this one)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare")
    commands.add_parser("recognize")
    overlay_parser = commands.add_parser("overlay")
    overlay_parser.add_argument("pages", nargs="*")
    run_parser = commands.add_parser("run")
    run_parser.add_argument("workflows", nargs="*", metavar="WORKFLOW",
                            help=f"default: all of {', '.join(WORKFLOWS)}")
    run_parser.add_argument("--optimize", type=int, default=1,
                            help="ocrmypdf --optimize for the Paddle workflows; use the level "
                                 "pdf-auto.sh reports for the shell workflows")
    commands.add_parser("score")
    args = parser.parse_args(argv)
    {"prepare": prepare, "recognize": recognize, "overlay": overlay, "run": run,
     "score": score}[args.command](args)


if __name__ == "__main__":
    main()

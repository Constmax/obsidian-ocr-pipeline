#!/usr/bin/env python3
"""Apple Vision against Tesseract and RapidOCR: speed, words and reading order.

Why is the iPhone scanner fast and accurate where the Stage-1 engines are
not? This measures the same Vision models on this Mac, on the existing truth
sets, with the existing scoring:

  python bench/apple_vision.py words     # 40 vector pages of bench_ocr.py
  python bench/apple_vision.py order --truth bench/reading_order_truth.json \\
      --run-dir <reading_order run dir with pages/ and truth-lines/>

Engines, each on the same 300-dpi page image, one page at a time:

  apple-text       RecognizeTextRequest (accurate, de-DE, language correction),
                   lines in Vision's order: what ocrmypdf-appleocr uses
  apple-documents  RecognizeDocumentsRequest (macOS 26): Vision's own document
                   structure and reading order
  tesseract        `tesseract PAGE stdout -l deu`, default page segmentation
  rapidocr         PP-OCRv5 through ocrmypdf_paddle, ordered by order_lines()

`words` needs bench/bench-lauf/ from `bench_ocr.py` (bench-seiten.pdf, the
text-layer truth wahr/bench-seiten.md and herkunft.json) and scores word
accuracy, word order and citation fidelity as bench_ocr.py does. Its Stage-2
output ocr/bench-seiten.md is listed for reference, without timing.

`order` reads a reading_order.py run directory and scores reading order as
reading_order.py does. The truth lines are RapidOCR's own lines, so rapidocr
is not listed there; `order_lines` on the truth lines is its ordering.

Seconds are wall time per page on the page image, model loading excluded
(every engine first reads one warm-up page). The Swift tool is compiled on
first use with swiftc (Xcode command-line tools, macOS 26 SDK). Outputs stay
in bench/apple-vision-lauf/: page images and text are course material.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

from paths import BENCH, REPOSITORY

SWIFT_SOURCE = BENCH / "apple_vision.swift"
RUN_DIR = BENCH / "apple-vision-lauf"
PADDLE_SRC = REPOSITORY / "ocrmypdf_paddle" / "src"
BENCH_RUN = BENCH / "bench-lauf"
ENGINES = ("apple-text", "apple-documents", "tesseract", "rapidocr")


# ── Engines ────────────────────────────────────────────────────────────────


def _swift_tool():
    binary = RUN_DIR / "apple_vision"
    binary.parent.mkdir(parents=True, exist_ok=True)
    if not binary.exists() or binary.stat().st_mtime < SWIFT_SOURCE.stat().st_mtime:
        subprocess.run(["swiftc", "-O", "-parse-as-library", str(SWIFT_SOURCE), "-o",
                        str(binary)], check=True)
    return binary


def run_apple(images, out):
    """apple-text and apple-documents texts and seconds per page."""
    raw = out / "apple-raw"
    binary = _swift_tool()
    subprocess.run([str(binary), str(out / "warm-up"), str(images[0])], check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run([str(binary), str(raw), *map(str, images)], check=True)
    texts, seconds = {"apple-text": {}, "apple-documents": {}}, {"apple-text": {}, "apple-documents": {}}
    for image in images:
        record = json.loads((raw / f"{image.stem}.json").read_text(encoding="utf-8"))
        texts["apple-text"][image.stem] = "\n".join(l["text"] for l in record["text"]["lines"])
        seconds["apple-text"][image.stem] = record["text"]["seconds"]
        texts["apple-documents"][image.stem] = "\n".join(
            d["transcript"] for d in record["documents"]["documents"])
        seconds["apple-documents"][image.stem] = record["documents"]["seconds"]
    return texts, seconds


def run_tesseract(images):
    subprocess.run(["tesseract", str(images[0]), "stdout", "-l", "deu"], check=True,
                   capture_output=True)
    texts, seconds = {}, {}
    for image in images:
        started = time.monotonic()
        texts[image.stem] = subprocess.run(["tesseract", str(image), "stdout", "-l", "deu"],
                                           check=True, capture_output=True, text=True).stdout
        seconds[image.stem] = time.monotonic() - started
        print(f"{image.stem} tesseract {seconds[image.stem]:.2f} s")
    return {"tesseract": texts}, {"tesseract": seconds}


def run_rapidocr(images, out, python):
    """RapidOCR in the Python that has it, through the `_rapidocr` command."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(PADDLE_SRC), env.get("PYTHONPATH")]))
    env.setdefault("OCRMYPDF_PADDLE_MODEL_DIR",
                   str(Path.home() / ".cache" / "ocrmypdf-paddle" / "models"))
    target = out / "rapidocr-raw"
    subprocess.run([python, __file__, "_rapidocr", str(target), *map(str, images)], env=env,
                   check=True)
    texts, seconds = {}, {}
    for image in images:
        record = json.loads((target / f"{image.stem}.json").read_text(encoding="utf-8"))
        texts[image.stem], seconds[image.stem] = record["text"], record["seconds"]
    return {"rapidocr": texts}, {"rapidocr": seconds}


def _rapidocr(args):
    from ocrmypdf_paddle import runtime
    from ocrmypdf_paddle.ordering import order_lines

    problems = runtime.missing_runtime() + runtime.check_models(runtime.model_dir())
    if problems:
        raise SystemExit("RapidOCR is not ready:\n  " + "\n  ".join(problems))
    recognizer = runtime.Recognizer()
    args.out.mkdir(parents=True, exist_ok=True)
    recognizer.recognize(args.images[0])
    for image in args.images:
        started = time.monotonic()
        page = recognizer.recognize(image)
        ordered = order_lines(page.lines, page.width, page.height)
        seconds = time.monotonic() - started
        (args.out / f"{image.stem}.json").write_text(json.dumps(
            {"seconds": seconds, "text": "\n".join(line.text for line in ordered)},
            ensure_ascii=False), encoding="utf-8")
        print(f"{image.stem} rapidocr {seconds:.2f} s")


def recognize_all(images, out, engines, rapid_python):
    texts, seconds = {}, {}
    for name, step in (("apple", lambda: run_apple(images, out)),
                       ("tesseract", lambda: run_tesseract(images)),
                       ("rapidocr", lambda: run_rapidocr(images, out, rapid_python))):
        if not any(engine.startswith(name) for engine in engines):
            continue
        t, s = step()
        texts.update(t)
        seconds.update(s)
    for engine in texts:
        folder = out / "text" / engine
        folder.mkdir(parents=True, exist_ok=True)
        for page, text in texts[engine].items():
            (folder / f"{page}.txt").write_text(text, encoding="utf-8")
    (out / "seconds.json").write_text(json.dumps(seconds, indent=1), encoding="utf-8")
    return {e: texts[e] for e in engines if e in texts}, seconds


def _speed(values):
    if not values:
        return "–", "–"
    return f"{statistics.median(values):.2f}", f"{sum(values):.0f}"


# ── Commands ───────────────────────────────────────────────────────────────


def words(args):
    """Word accuracy, word order and citation fidelity on the vector pages."""
    from bench_ocr import seiten_trennen, vergleiche

    out = args.out or RUN_DIR / "words"
    pages_dir = out / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    origin = json.loads((BENCH_RUN / "herkunft.json").read_text(encoding="utf-8"))
    images = []
    for entry in origin:
        image = pages_dir / f"w{entry['seite']:02d}.png"
        if not image.exists():
            stem = image.with_suffix("")
            subprocess.run(["pdftoppm", "-r", "300", "-gray", "-png", "-singlefile",
                            "-f", str(entry["seite"]), "-l", str(entry["seite"]),
                            str(BENCH_RUN / "bench-seiten.pdf"), str(stem)], check=True)
        images.append(image)
    texts, seconds = recognize_all(images, out, args.engines, args.rapid_python)

    truth = seiten_trennen(BENCH_RUN / "wahr" / "bench-seiten.md")
    stage2 = seiten_trennen(BENCH_RUN / "ocr" / "bench-seiten.md")
    texts["pdf2md (Stufe 2)"] = {f"w{n:02d}": text for n, text in stage2.items()}

    print("\n| Engine | Seiten | Wortgenauigkeit | Reihenfolge (Median) | Zitattreue | "
          "Sek./Seite (Median) | Sek. gesamt |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    report = {}
    for engine, pages in texts.items():
        rows = []
        for entry in origin:
            page, n = f"w{entry['seite']:02d}", entry["seite"]
            if n in truth and page in pages:
                row = vergleiche(truth[n], pages[page])
                if row:
                    rows.append(row | {"page": page})
        total = sum(r["woerter"] for r in rows)
        hit = sum(r["wortgenauigkeit"] * r["woerter"] for r in rows)
        citations = sum(r["zitate"] for r in rows)
        kept = sum((r["zitattreue"] or 0) * r["zitate"] for r in rows)
        order = statistics.median(r["reihenfolge"] for r in rows)
        median, whole = _speed(list(seconds.get(engine, {}).values()))
        print(f"| `{engine}` | {len(rows)} | {hit / total:.1%} | {order:.1%} | "
              f"{kept / citations:.1%} | {median} | {whole} |")
        report[engine] = rows
    (out / "words.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                    encoding="utf-8")


def order(args):
    """Reading order on a reading_order.py truth set."""
    import reading_order as ro

    specs = ro.load_truth(args.truth)
    out = args.out or RUN_DIR / args.truth.stem
    images = [args.run_dir / "pages" / f"{spec['id']}.png" for spec in specs]
    engines = [e for e in args.engines if e != "rapidocr"]
    texts, seconds = recognize_all(images, out, engines, args.rapid_python)
    runs = args.run_dir / "runs"
    existing = [n for n in ro.WORKFLOWS if (runs / n).is_dir()]

    results = {}
    for spec in specs:
        record = ro._recognized(args, spec["id"])
        truth, _ = ro.truth_lines(spec["regions"], record["lines"], record["width"],
                                  record["height"])
        page_texts = {engine: texts[engine][spec["id"]] for engine in texts}
        for name in existing:
            pdf = runs / name / f"{spec['id']}.pdf"
            if pdf.exists():
                page_texts[name] = ro._pdftotext(pdf)
        page_texts.update(ro._line_orders(record))
        for name, text in page_texts.items():
            results.setdefault(name, {})[spec["id"]] = ro.score_page(truth, text)

    print("\n| Workflow | Seiten | Median | Mittel | Min | verschränkte Seiten | "
          "Vollbreite falsch | nicht gefunden | Sek./Seite (Median) |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name, pages in results.items():
        s = ro.summarize(pages)
        median, _ = _speed(list(seconds.get(name, {}).values()))
        print(f"| `{name}` | {s['pages']} | {ro._percent(s['median'])} | "
              f"{ro._percent(s['mean'])} | {ro._percent(s['min'])} | {s['interleaved_pages']} | "
              f"{s['full_width_misplaced']} of {s['full_width']} | "
              f"{s['unmatched']} of {s['eligible']} | {median} |")
    print("\n| Seite | Layout | " + " | ".join(f"`{n}`" for n in results) + " |")
    print("|---|---|" + "---:|" * len(results))
    for spec in specs:
        cells = []
        for name in results:
            page = results[name].get(spec["id"])
            cells.append("–" if page is None else
                         f"{ro._percent(page['accuracy'])}{'*' if page['interleaved_sections'] else ''}")
        print(f"| {spec['id']} | {spec['layout']} | " + " | ".join(cells) + " |")
    (out / "order.json").write_text(json.dumps(
        {name: {page: {k: v for k, v in score.items() if k != "positions"}
                for page, score in pages.items()} for name, pages in results.items()},
        indent=1), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("words", "order"):
        sub = commands.add_parser(name)
        sub.add_argument("--out", type=Path, help=f"default: below {RUN_DIR}")
        sub.add_argument("--engines", nargs="+", choices=ENGINES, default=list(ENGINES))
        sub.add_argument("--rapid-python", default=sys.executable,
                         help="Python with rapidocr and onnxruntime (default: this one)")
    commands.choices["order"].add_argument("--truth", type=Path, required=True)
    commands.choices["order"].add_argument("--run-dir", type=Path, required=True,
                                           help="reading_order.py run with pages/, truth-lines/")
    commands.choices["order"].add_argument("--python", default=sys.executable,
                                           help="Python for reading_order page checks")
    internal = commands.add_parser("_rapidocr")
    internal.add_argument("out", type=Path)
    internal.add_argument("images", type=Path, nargs="+")
    args = parser.parse_args(argv)
    {"words": words, "order": order, "_rapidocr": _rapidocr}[args.command](args)


if __name__ == "__main__":
    main()

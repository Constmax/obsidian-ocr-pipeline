#!/usr/bin/env python3
"""Path C, End-to-End: PDF → Markdown (CLI + page runner).

  source .venv-mlxocr/bin/activate && python .ocr-bench/pdf2md.py <pdf> [--dpi 300]

Renders each page, tiles on high text density, runs PaddleOCR-VL and
assembles lines using their <|LOC|> coordinates into Markdown.

Since Issue #8, this file contains only CLI and page runner: geometry
(columns, boxes, diagrams) resides in layout.py, tiling and model in
ocr.py, Markdown assembly in zusammenbau.py. Assembly is the
testable layer — pdf2md/test runs without MLX, fitz, and Vault assets.

Writes to .ocr-bench/out-C/, leaves raw/ untouched.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

import abbruch
import dictionary
import zusammenbau
from zusammenbau import (as_callout, build_document, clean_text, merge_fragments,
                        build_frontmatter, page_marker, assemble_paragraphs)
from layout import (image_ratio, detect_boxes, assign_boxes,
                   detect_layout, split_columns, tables_markdown)
from ocr import (OVERLAP, TOKEN_MAX, CHARACTERS_PER_INK, _ink_amount,
               tile_lines, tile_vertically, tile_horizontally,
               trim_overlap)

BENCH = Path(__file__).resolve().parent
OUT = BENCH / "out-C"
TMP = OUT
MODEL = os.environ.get("MLX_OCR_MODEL", "mlx-community/PaddleOCR-VL-1.5-4bit")
PROMPT = "Parse this document page to Markdown."
TILE_THRESHOLD = 3000      # Characters in existing textlayer


def parse_pages(s):
    """Convert '1,3-5,8' into a set of int (1-based).

    Empty string yields None (all pages). Invalid inputs or
    page numbers outside valid range result in an error on stderr and exit code 1.
    """
    s = s.strip() if s else ""
    if not s:
        return None
    selection = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            try:
                lo, hi = int(lo), int(hi)
            except ValueError:
                sys.exit(f"invalid page specification: {part!r}")
            if lo < 1 or hi < 1 or lo > hi:
                sys.exit(f"invalid page number: {part}")
            selection.update(range(lo, hi + 1))
        else:
            try:
                n = int(part)
            except ValueError:
                sys.exit(f"invalid page specification: {part!r}")
            if n < 1:
                sys.exit(f"invalid page number: {n}")
            selection.add(n)
    return selection


def running_lines(doc, header_zone=0.09, footer_zone=0.93, min_pages=2):
    """Texts appearing identically on multiple pages in header or footer zone."""
    from collections import Counter
    counter = Counter()
    for i in range(doc.page_count):
        p = doc[i]
        H = p.rect.height or 1
        seen = set()
        for b in p.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b["lines"]:
                rel = ((ln["bbox"][1] + ln["bbox"][3]) / 2) / H
                if not (rel <= header_zone or rel >= footer_zone):
                    continue
                t = re.sub(r"\s+", " ",
                           "".join(s["text"] for s in ln["spans"])).strip()
                if len(t) < 6 or re.fullmatch(r"[\d\s\-–—.]+", t):
                    continue
                seen.add(t)
        counter.update(seen)
    return {t for t, n in counter.items() if n >= min_pages}


def textlayer_lines(page):
    """Like parse_lines, but from existing textlayer."""
    W = page.rect.width or 1
    H = page.rect.height or 1
    tables = tables_markdown(page)
    frames = [t[2] for t in tables]

    def in_table(bbox):
        mx, my = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        return any(x0 <= mx <= x1 and y0 <= my <= y1
                   for x0, y0, x1, y1 in frames)

    lines, prose, rotated = [], [], []
    for y, md, bbox in tables:
        lines.append([md, (int(bbox[0] / W * 1000), int(bbox[1] / H * 1000),
                            int(bbox[2] / W * 1000), int(bbox[3] / H * 1000)),
                       "tabelle"])
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            if in_table(ln["bbox"]):
                continue
            parts = []
            for sp in ln["spans"]:
                t = clean_text(sp["text"])
                if not t.strip():
                    parts.append(t)
                    continue
                if t.strip() in ("o", "O") and "courier" in sp.get("font", "").lower():
                    parts.append("-")
                    continue
                bold = bool(sp.get("flags", 0) & 16) or "bold" in sp.get("font", "").lower()
                before = t[:len(t) - len(t.lstrip())]
                after = t[len(t.rstrip()):]
                parts.append(f"{before}**{t.strip()}**{after}" if bold else t)
            text = "".join(parts).strip()
            if not text:
                continue
            (prose if tuple(ln.get("dir", (1, 0))) == (1, 0) else rotated) \
                .append([text, tuple(ln["bbox"])])
    for text, box in merge_fragments(prose, W) + rotated:
        lines.append([text, (int(box[0] / W * 1000), int(box[1] / H * 1000),
                              int(box[2] / W * 1000), int(box[3] / H * 1000))])
    return lines


def analyze_pages(pdf, dpi, ocr_only=False, selection=None):
    """Decide for each page: textlayer sufficient, or run through model?"""
    import fitz
    doc = fitz.open(pdf)
    for p in doc:
        if p.rotation:
            p.remove_rotation()
    if selection:
        invalid = [n for n in selection if n < 1 or n > doc.page_count]
        if invalid:
            sys.exit(f"page numbers {sorted(invalid)} do not exist "
                     f"(PDF has {doc.page_count} pages)")
        selection = {n for n in selection if 1 <= n <= doc.page_count}
    zusammenbau.set_running(running_lines(doc))
    pages = []
    for i in range(doc.page_count):
        if selection is not None and (i + 1) not in selection:
            continue
        p = doc[i]
        chars = len(p.get_text("text").strip())
        scan = image_ratio(p) >= 0.5 or chars < 100
        table_frames = [] if scan else [t[2] for t in tables_markdown(p)]
        boxes, diagram = detect_boxes(p, scan, table_frames)
        if not scan and not ocr_only:
            pages.append((i + 1, None, chars, "vektoriell", None,
                          textlayer_lines(p), boxes, diagram))
            continue
        png = TMP / f"_seite{i+1:03d}.png"
        p.get_pixmap(dpi=dpi).save(png)
        layout_type, gutter = detect_layout(p)
        pages.append((i + 1, png, chars, layout_type, gutter, None, boxes, diagram))
    doc.close()
    return pages


def diagram_image(pdf, nr, image_dir, max_edge=1800):
    """Save page as PNG and return Obsidian embed link."""
    import fitz
    image_dir.mkdir(parents=True, exist_ok=True)
    name = f"{pdf.stem}-s{nr:03d}.png".replace(" ", "-")
    doc = fitz.open(pdf)
    page = doc[nr - 1]
    long_side = max(page.rect.width, page.rect.height) or 1
    z = min(max_edge / long_side, 4.0)
    page.get_pixmap(matrix=fitz.Matrix(z, z)).save(image_dir / name)
    doc.close()
    return name, image_dir / name


def _progress(event: dict):
    """Write JSON-formatted progress to stderr."""
    sys.stderr.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--tile-from", "--kachel-ab", dest="tile_from", type=int, default=TILE_THRESHOLD)
    ap.add_argument("--no-bold", "--kein-fett", dest="no_bold", action="store_true",
                    help="Disable bold detection via ink density")
    ap.add_argument("--ocr-only", "--nur-ocr", dest="ocr_only", action="store_true",
                    help="Ignore textlayer, run everything through model")
    ap.add_argument("--retries", "--neuversuche", dest="retries", type=int, default=1,
                    help="How often a derailed tile is recalculated finer")
    ap.add_argument("--lines-dump", "--zeilen-dump", dest="lines_dump", type=Path, default=None,
                    help="Save lines with boxes per page as JSON")
    ap.add_argument("--no-dictionary", "--kein-woerterbuch", dest="no_dictionary", action="store_true",
                    help="Disable dictionary check completely")
    ap.add_argument("--dictionary", "--woerterbuch", dest="dictionary", action="append", default=[], type=Path,
                    metavar="FILE",
                    help="Additional wordlist or .dic")
    ap.add_argument("--dictionary-correct", "--woerterbuch-korrigieren", dest="dictionary_correct", action="store_true",
                    help="Replace unambiguous cases instead of only reporting")
    ap.add_argument("--dictionary-report", "--woerterbuch-bericht", dest="dictionary_report", type=Path, default=None,
                    metavar="FILE",
                    help="Save all findings with page number as JSON")
    ap.add_argument("--out", type=Path, default=OUT,
                    help="Target folder for .md")
    ap.add_argument("--image-dir", "--bild-dir", dest="image_dir", type=Path, default=None,
                    help="Storage for diagram images")
    ap.add_argument("--image-max-edge", "--bild-max-kante", dest="image_max_edge", type=int, default=1800,
                    help="Longest pixel edge of diagram images")
    ap.add_argument("--diagram-pages", "--diagramm-seiten", dest="diagram_pages", default="",
                    help="Pages ALWAYS considered as diagrams")
    ap.add_argument("--diagram-image-only", "--diagramm-nur-bild", dest="diagram_image_only", action="store_true",
                    help="Diagram pages without text callout")
    ap.add_argument("--pages", "--seiten", dest="pages", default="",
                    help="Convert only these pages")
    ap.add_argument("--progress", "--fortschritt", dest="progress", action="store_true",
                    help="Output machine-readable progress as JSON lines on stderr")
    a = ap.parse_args()

    pdf = Path(a.pdf)
    if not pdf.exists():
        sys.exit(f"not found: {pdf}")
    if a.image_dir is None:
        a.image_dir = a.out / "assets"
    forced = set()
    for part in filter(None, (x.strip() for x in a.diagram_pages.split(","))):
        if "-" in part:
            from_page, to_page = (int(x) for x in part.split("-", 1))
            forced.update(range(from_page, to_page + 1))
        else:
            forced.add(int(part))
    selection = parse_pages(a.pages)
    abbruch.install()

    global TMP
    OUT.mkdir(parents=True, exist_ok=True)
    tmp_dir = tempfile.TemporaryDirectory(prefix=f"_tmp-{pdf.stem}-", dir=OUT)
    TMP = Path(tmp_dir.name)
    try:
        a.out.mkdir(parents=True, exist_ok=True)

        selection_text = f" (pages {sorted(selection)})" if selection else ""
        print(f"Analyzing {pdf.name} (scan pages @ {a.dpi} dpi){selection_text} ...")
        pages = analyze_pages(pdf, a.dpi, a.ocr_only, selection)
        n_ocr = sum(1 for s in pages if s[1] is not None)
        print(f"   {len(pages)} pages — {len(pages)-n_ocr} from textlayer, "
              f"{n_ocr} through model\n")

        if a.progress:
            _progress({"typ": "start", "datei": pdf.name, "seiten": len(pages), "dpi": a.dpi})

        wb = None
        if n_ocr and not a.no_dictionary:
            wb = dictionary.load(a.dictionary)
            print("Dictionary: " + (wb.source if wb else
                  "none found — check skipped."))
            if wb and not a.dictionary_correct:
                print("   only report — replace with --dictionary-correct\n")
            elif wb:
                print("   unambiguous cases will be replaced\n")

        ocr = None
        if n_ocr:
            from mlx_vlm import generate, load
            from mlx_vlm.prompt_utils import apply_chat_template
            from mlx_vlm.utils import load_config
            model, processor = load(MODEL)
            config = load_config(MODEL)
            formatted = apply_chat_template(processor, config, PROMPT, num_images=1)

            def ocr(img, max_tokens=TOKEN_MAX):
                res = generate(model, processor, formatted, image=[str(img)],
                               max_tokens=max_tokens, temperature=0.0,
                               verbose=False)
                return res if isinstance(res, str) else getattr(res, "text", str(res))

        md, t_total, n_diag, n_derailed = [], time.perf_counter(), 0, 0
        n_suspect, n_corrected, report = 0, 0, []
        last_page = 0

        def save_page(nr, paragraphs, diagram, dt, chars, source, discarded, trace=(),
                      marker_extra=None, findings=()):
            nonlocal n_diag, last_page
            last_page = nr
            if diagram:
                marker_extra = "diagramm"
            header = page_marker(nr, marker_extra)
            extra_str = ""
            if diagram:
                n_diag += 1
                name, path = diagram_image(pdf, nr, a.image_dir, a.image_max_edge)
                parts = [f"![[{name}]]"]
                if not a.diagram_image_only and paragraphs:
                    parts.append(as_callout(
                        paragraphs, "Text der Seite (Reihenfolge nicht verlässlich)"))
                md.append(header + "\n\n".join(parts))
                extra_str = f" | → {path.name} ({path.stat().st_size // 1024} kB)"
            else:
                md.append(header + "\n\n".join(paragraphs))
            print(f"→ p.{nr}: {dt:5.1f} s | {chars:5d} chars textlayer → "
                  f"{sum(len(p) for p in paragraphs):5d} chars | "
                  f"{len(paragraphs):3d} paragraphs | "
                  f"{'DIAGRAM as image' if diagram else source}{extra_str}")
            if discarded:
                print(f"     discarded ({len(discarded)}): "
                      + " ¦ ".join(w[:34] for w in discarded[:6])
                      + (" …" if len(discarded) > 6 else ""))
            if findings:
                print(f"     ⌕ {len(findings)} words: " + " ¦ ".join(
                    (f"{b.word} → {b.suggestion}" if b.suggestion else f"{b.word} ?")
                    + (" ✓" if b.corrected else "")
                    + ("" if b.count == 1 else f" ({b.count}x)")
                    for b in findings[:6]) + (" …" if len(findings) > 6 else ""))
            for line in trace:
                print(f"     ⚠ {line}")

            if a.progress:
                if diagram:
                    origin = "diagramm"
                elif marker_extra == "textlayer":
                    origin = "textlayer"
                else:
                    origin = "ocr"
                is_der = bool(trace)
                if is_der:
                    _progress({
                        "typ": "seite",
                        "nr": nr,
                        "von": len(pages),
                        "sekunden": round(dt, 1),
                        "herkunft": origin,
                        "entgleist": True,
                        "grund": trace[0]
                    })
                else:
                    _progress({
                        "typ": "seite",
                        "nr": nr,
                        "von": len(pages),
                        "sekunden": round(dt, 1),
                        "herkunft": origin,
                        "entgleist": False
                    })

        dump = []

        for nr, png, chars, layout_type, gutter, textlayer, boxes, diagram in pages:
            if abbruch.requested():
                break
            t = time.perf_counter()
            diagram = diagram or nr in forced
            if textlayer is not None:
                lines = split_columns(assign_boxes(textlayer, boxes))
                dump.append({"seite": nr, "quelle": "textlayer", "zeilen": lines})
                paragraphs = assemble_paragraphs(lines)
                save_page(nr, paragraphs, diagram, time.perf_counter() - t, chars,
                          "Textlayer, without model",
                          getattr(assemble_paragraphs, "discarded", []),
                          marker_extra="textlayer")
                continue

            if diagram and a.diagram_image_only:
                save_page(nr, [], True, time.perf_counter() - t, chars, "", [],
                          marker_extra="ocr")
                continue

            if layout_type == "zweispaltig":
                mode = f"senkrecht @{gutter:.0%}"
                ov = int(OVERLAP * 1000)
                g = int(gutter * 1000)
                tiles = list(zip(tile_vertically(png, gutter),
                                 [(0, min(g + ov, 1000)), (max(g - ov, 0), 1000)]))
            elif chars >= a.tile_from:
                mode = "waagerecht"
                tiles = [(p, None) for p, _, _ in tile_horizontally(png)]
            else:
                mode = "ganz"
                tiles = [(png, (0, 1000))]

            ink = _ink_amount(png, a.dpi)
            calibrated = chars >= 400 and ink > 0
            factor = chars / ink if calibrated else CHARACTERS_PER_INK

            lines, trace = [], []
            for part, window in tiles:
                parsed, s = tile_lines(part, ocr, not a.no_bold, factor,
                                       a.dpi, calibrated,
                                       max_depth=a.retries)
                trace += s
                if window:
                    parsed = assign_boxes(parsed, boxes, window)
                ordered = (split_columns(parsed) if len(tiles) == 1
                           else sorted(parsed,
                                       key=lambda z: z[1][1] if z[1] else 0))
                lines += trim_overlap(lines, ordered)
            if trace:
                n_derailed += 1
            dump.append({"seite": nr, "quelle": f"{layout_type}, {mode}", "zeilen": lines})
            paragraphs = assemble_paragraphs(lines)
            discarded = getattr(assemble_paragraphs, "discarded", [])
            paragraphs, findings = dictionary.check(paragraphs, wb,
                                                    a.dictionary_correct)
            n_corrected += sum(b.count for b in findings if b.corrected)
            n_suspect += sum(b.count for b in findings if not b.corrected)
            report += [{"seite": nr, "wort": b.word, "anzahl": b.count,
                        "vorschlag": b.suggestion, "korrigiert": b.corrected}
                       for b in findings]
            save_page(nr, paragraphs, diagram, time.perf_counter() - t, chars,
                      f"{layout_type}, {mode}", discarded, trace, f"ocr | {layout_type}, {mode}",
                      findings)

        if abbruch.requested() and pages and last_page < pages[-1][0]:
            written = [s for s in pages if s[0] <= last_page]
            if not written:
                print("Cancellation before first page — no partial file written.")
                sys.exit(7)
            header = build_frontmatter(
                title=pdf.stem,
                source_pdf_path=pdf,
                pages=len(written),
                pages_textlayer=sum(1 for s in written
                                    if s[5] is not None),
                pages_ocr=sum(1 for s in written if s[5] is None),
                pages_diagram=n_diag,
                pages_derailed=n_derailed,
                words_suspect=n_suspect,
                words_corrected=n_corrected,
                ocr_model=MODEL if n_ocr else None,
                ocr_date=date.today().isoformat(),
                ocr_timestamp=datetime.now().isoformat(timespec="seconds"),
                aborted=f"seite {last_page} von {len(pages)}",
            )
            source_link = f"Quelle: [[{pdf.as_posix()}]]\n"
            target = a.out / f"{pdf.stem}.md"
            target.write_text(build_document(header, source_link, md),
                              encoding="utf-8")
            print(f"\nCancellation: partial file written "
                  f"({last_page} of {len(pages)} pages) → {target}")
            sys.exit(6)

        total_time = time.perf_counter() - t_total
        header = build_frontmatter(
            title=pdf.stem,
            source_pdf_path=pdf,
            pages=len(pages),
            pages_textlayer=len(pages) - n_ocr,
            pages_ocr=n_ocr,
            pages_diagram=n_diag,
            pages_derailed=n_derailed,
            words_suspect=n_suspect,
            words_corrected=n_corrected,
            ocr_model=MODEL if n_ocr else None,
            ocr_date=date.today().isoformat(),
            ocr_timestamp=datetime.now().isoformat(timespec="seconds"),
        )
        source_link = f"Quelle: [[{pdf.as_posix()}]]\n"
        target = a.out / f"{pdf.stem}.md"
        target.write_text(build_document(header, source_link, md),
                          encoding="utf-8")
        if a.lines_dump:
            a.lines_dump.write_text(json.dumps(dump, ensure_ascii=False),
                                    encoding="utf-8")
            print(f"→ {a.lines_dump} ({len(dump)} pages)")
        if a.dictionary_report:
            a.dictionary_report.write_text(
                json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"→ {a.dictionary_report} ({len(report)} findings)")
        if a.progress:
            _progress({
                "typ": "fertig",
                "ziel": str(a.out / f"{pdf.stem}.md"),
                "sekunden": round(total_time, 1),
                "entgleist": n_derailed
            })
        print(f"\n{total_time:.1f} s total ({total_time/len(pages):.1f} s/page)\n→ {target}")
    finally:
        tmp_dir.cleanup()


if __name__ == "__main__":
    main()

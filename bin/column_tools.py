#!/usr/bin/env python3
"""
column_tools.py — Detect, split, and reassemble two-column PDF pages.

Subcommands:
    detect <pdf>               Print page numbers with two-column layout.
    split  <in> <out> --map M  Split pages via Ghostscript CropBox.
            [--all | --auto | --pages 3,5-9]
    merge  <ocr> <out> --map M Reassemble half-pages into full pages (pikepdf).

Dependencies: pikepdf, PIL — already in ocrmypdf venv.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import pikepdf

# ── Detection Constants ──────────────────────────────────────
# Detection works line-by-line, not on a page-wide word-center scatter:
# a page-wide search for "the widest gap in the middle band" fails in
# practice because dense text (many short lines, hyphenation, staggered
# starts) never leaves one consistent global x-gap even on genuine
# two-column pages (verified empirically: max gap ~4pt on a known
# two-column page, far below any reasonable threshold). Per-line analysis
# is robust because each individual line is either confined to one column
# or spans the full width (headers) — there is no "gap search" needed,
# just a left/right/full classification per line.
LINE_Y_TOL = 3.0               # pts; words within this y-delta = same line
SIDE_MARGIN_PCT = 0.04         # tolerance around page mid for left/right classification
MIN_LINES_PER_SIDE = 3         # min column-confined lines required on each side
COLUMN_LINE_RATIO = 0.4        # min fraction of all lines that must be column-confined
GUTTER_MIN_WIDTH_PCT = 0.03    # min real gutter width (% of page width) between the columns
GUTTER_MAX_WIDTH_PCT = 0.15    # max real gutter width (% of page width) between the columns
GUTTER_BAND_LEFT = 0.35        # left boundary for pixel gutter search
GUTTER_BAND_RIGHT = 0.65       # right boundary for pixel gutter search
DETECT_DPI = 100                # DPI for pixel fallback rendering (pre-OCR scans w/o text layer)

# Pairwise nearest-row gutter matching (see _analyze_columns): a single
# global left/right edge (even percentile-trimmed) collapses under skew —
# verified empirically (photographed Aktenordner pages routinely produce a
# *negative* global gutter despite being genuinely two-column, because the
# skew shifts the column boundary across the page height). Matching each
# left line to its nearest same-row right line and checking gutter
# plausibility *per pair* is tolerant of skew (each pair is a local
# comparison) and — as a side effect — also rejects "sliver" false
# positives (a bled-through neighboring page, or partial cover-sheet
# edge): those pairs have no real structural alignment, so their gutters
# scatter widely and rarely land in the plausible range.
PAIR_Y_TOL = 8.0                # pts; max y-center delta to call two lines "same row"
PAIR_VALID_FRAC_MIN = 0.30      # min fraction of pairs with a plausible gutter
MIN_VALID_PAIRS = 5             # min number of matched pairs to trust the statistic


# ═══════════════════════════════════════════════════════════════
#  DETECT
# ═══════════════════════════════════════════════════════════════

def detect_columns(pdf_path: str) -> list:
    """Return list of 1-indexed page numbers with two-column layout.

    Uses pdftotext -bbox line clustering (fast, text-based) when a text
    layer exists. Falls back to pixel analysis for pre-OCR scans (no text
    layer yet) — that is the common case for the main pipeline, since
    column detection runs BEFORE OCR."""
    from pikepdf import Pdf
    with Pdf.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)

    two_col = []
    for p in range(1, total_pages + 1):
        analysis = _analyze_columns(pdf_path, p)
        if analysis is None:
            # No usable text layer — fall back to pixel-based detection
            try:
                if _pixel_two_column(pdf_path, p):
                    two_col.append(p)
            except Exception:
                continue
        elif analysis["is_two_column"]:
            two_col.append(p)
    return two_col


def _extract_words(pdf_path: str, page_num: int):
    """Return (page_width, words) via pdftotext -bbox.
    words: list of (xMin, xMax, yMin, yMax)."""
    result = subprocess.run(
        ["pdftotext", "-f", str(page_num), "-l", str(page_num),
         "-bbox", pdf_path, "-"],
        capture_output=True, text=True, timeout=30
    )
    words = []
    page_width = None
    for m in re.finditer(
        r'<page width="([^"]+)"|'
        r'<word xMin="([^"]+)" yMin="([^"]+)" xMax="([^"]+)" yMax="([^"]+)"',
        result.stdout
    ):
        if m.group(1):
            page_width = float(m.group(1))
        else:
            words.append((float(m.group(2)), float(m.group(4)),
                          float(m.group(3)), float(m.group(5))))
    return page_width, words


def _cluster_lines(words: list) -> list:
    """Group words into visual lines by yMin proximity.
    Returns list of dict(xMin, xMax, yMin, yMax)."""
    if not words:
        return []
    ws = sorted(words, key=lambda w: w[2])  # sort by yMin
    lines = []
    cur = None
    for xmin, xmax, ymin, ymax in ws:
        if cur is not None and (ymin - cur["yMin"]) <= LINE_Y_TOL:
            cur["xMin"] = min(cur["xMin"], xmin)
            cur["xMax"] = max(cur["xMax"], xmax)
            cur["yMax"] = max(cur["yMax"], ymax)
        else:
            if cur is not None:
                lines.append(cur)
            cur = {"xMin": xmin, "xMax": xmax, "yMin": ymin, "yMax": ymax}
    if cur is not None:
        lines.append(cur)
    return lines


def _analyze_columns(pdf_path: str, page_num: int):
    """Analyze one page's text layer for two-column layout.

    Returns None if there's no usable text layer (caller should fall
    back to pixel analysis). Otherwise returns a dict:
        {"is_two_column": bool, "split_x": float, "page_width": float}
    """
    page_width, words = _extract_words(pdf_path, page_num)
    if not page_width or len(words) < 20:
        return None

    lines = _cluster_lines(words)
    if len(lines) < 6:
        return None

    mid = page_width / 2
    margin = page_width * SIDE_MARGIN_PCT
    left_lines, right_lines = [], []
    for ln in lines:
        if ln["xMax"] <= mid + margin:
            left_lines.append(ln)
        elif ln["xMin"] >= mid - margin:
            right_lines.append(ln)
        # else: line spans the gutter (header/footer) — not column-confined

    total = len(lines)
    column_lines = len(left_lines) + len(right_lines)

    not_two_col = {"is_two_column": False, "split_x": round(mid, 1), "page_width": page_width}

    if (len(left_lines) < MIN_LINES_PER_SIDE or len(right_lines) < MIN_LINES_PER_SIDE
            or column_lines < COLUMN_LINE_RATIO * total):
        return not_two_col

    # Match each left line to its nearest same-row right line and check
    # gutter plausibility per pair (skew-tolerant; also rejects sliver/
    # bleed-through false positives — see module-level comment above
    # PAIR_Y_TOL). A pair only "counts" if the two lines are close enough
    # in y to plausibly be the same printed row.
    gutter_min = page_width * GUTTER_MIN_WIDTH_PCT
    gutter_max = page_width * GUTTER_MAX_WIDTH_PCT
    matched_gutters = []       # all matched pairs' raw gutter (for valid_frac)
    valid_midpoints = []       # midpoints of only the plausible-gutter pairs (for split_x)

    for ln in left_lines:
        ly = (ln["yMin"] + ln["yMax"]) / 2
        best = min(right_lines, key=lambda r: abs((r["yMin"] + r["yMax"]) / 2 - ly))
        ry = (best["yMin"] + best["yMax"]) / 2
        if abs(ry - ly) > PAIR_Y_TOL:
            continue
        gutter = best["xMin"] - ln["xMax"]
        matched_gutters.append(gutter)
        if gutter_min <= gutter <= gutter_max:
            valid_midpoints.append((ln["xMax"] + best["xMin"]) / 2)

    if len(matched_gutters) < MIN_VALID_PAIRS:
        return not_two_col

    valid_frac = len(valid_midpoints) / len(matched_gutters)
    if valid_frac < PAIR_VALID_FRAC_MIN:
        return not_two_col

    split_x = round(_median(valid_midpoints), 1)
    return {"is_two_column": True, "split_x": split_x, "page_width": page_width}


def _median(values: list) -> float:
    """Standalone median (avoids importing statistics for one call site)."""
    s = sorted(values)
    n = len(s)
    mid_i = n // 2
    if n % 2 == 1:
        return s[mid_i]
    return (s[mid_i - 1] + s[mid_i]) / 2.0


def _pixel_two_column(pdf_path: str, page_num: int) -> bool:
    """Fallback: render page as image, use edge detection to find gutter.
    Returns True if two-column, False if single-column.
    Edge detection ignores shadows/creases, sliding window handles skew."""
    from PIL import Image, ImageFilter
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pgm", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        # 50 DPI is enough for edge detection and much faster
        subprocess.run(
            ["pdftoppm", "-f", str(page_num), "-l", str(page_num),
             "-gray", "-r", "50", "-singlefile", pdf_path,
             os.path.splitext(tmp_path)[0]],
            capture_output=True, check=True, timeout=30
        )
        img = Image.open(tmp_path)
        # Find edges (text has lots of edges, shadows have none)
        edges = img.filter(ImageFilter.FIND_EDGES)
        edges = edges.point(lambda p: 255 if p > 50 else 0).convert("1", dither=False)
        w, h = edges.size
        
        try:
            px = list(edges.get_flattened_data())
        except AttributeError:
            px = list(edges.getdata())
    finally:
        os.unlink(tmp_path)

    rows = [px[y * w:(y + 1) * w] for y in range(h)]
    
    # Project edges vertically
    col_edges = [sum(1 for y in range(h) if rows[y][x] > 0) for x in range(w)]
    
    total_edges = sum(col_edges)
    if total_edges < w * 10:
        return False  # Blank or mostly empty page

    # Sliding window of 3% width (accommodates slight skew)
    win = max(1, int(w * 0.03))
    smooth_edges = [sum(col_edges[i:i+win]) for i in range(w - win + 1)]
    
    gs = int(w * 0.4)
    ge = int(w * 0.6) - win + 1
    
    if ge <= gs:
        return False
        
    center_min = min(smooth_edges[gs:ge])
    left_max = max(smooth_edges[:int(w*0.4)])
    
    # If center has a deep valley compared to left text peak, it's two columns
    ratio = center_min / max(1, left_max)
    
    if ratio < 0.4:
        return True
    return False


# ═══════════════════════════════════════════════════════════════
#  SPLIT (Ghostscript-based, driven from Python)
# ═══════════════════════════════════════════════════════════════

def _page_dims(pdf_path: str, page_num: int) -> tuple:
    """Return (width, height) in pts for a page."""
    r = subprocess.run(
        ["pdfinfo", "-f", str(page_num), "-l", str(page_num), pdf_path],
        capture_output=True, text=True
    )
    m = re.search(r"Page size:\s+([\d.]+)\s+x\s+([\d.]+)", r.stdout)
    if m:
        return float(m.group(1)), float(m.group(2))
    return 595.0, 842.0


def _split_position(pdf_path: str, page_num: int, w: float) -> float:
    """Find split x-position by reusing the line-based column analysis.
    Falls back to the page's exact midpoint if no reliable analysis exists
    (e.g. no text layer yet, or --all/--pages forced a split on a page
    that isn't actually two-column)."""
    analysis = _analyze_columns(pdf_path, page_num)
    if analysis:
        return analysis["split_x"]
    return round(w / 2, 1)


def _gs_crop(page_num: int, crop: str, output_path: str, input_pdf: str) -> None:
    """Run Ghostscript to extract/crop one page."""
    cmd = [
        "gs", "-sDEVICE=pdfwrite", "-dNOPAUSE", "-dQUIET", "-dBATCH",
        f"-dFirstPage={page_num}", f"-dLastPage={page_num}",
        f"-sOutputFile={output_path}", "-c", crop, "-f", input_pdf,
    ]
    subprocess.run(cmd, capture_output=True, timeout=60)
    if not os.path.exists(output_path):
        raise RuntimeError(f"Ghostscript failed for page {page_num}: {output_path}")


def split_pdf_gs(input_pdf: str, output_pdf: str, map_path: str,
                 split_pages: set, detect_first: bool = False):
    """Split pages via Ghostscript CropBox. Mixed full pages pass through."""
    from pikepdf import Pdf

    pdf = Pdf.open(input_pdf)
    total_pages = len(pdf.pages)

    if detect_first and split_pages is None:
        split_pages = set(detect_columns(input_pdf))

    page_map = []
    tmpdir = tempfile.mkdtemp(prefix="colsplit_")
    page_files = []

    for pn in range(1, total_pages + 1):
        w, h = _page_dims(input_pdf, pn)

        if split_pages and pn in split_pages:
            sx = _split_position(input_pdf, pn, w)

            lp = os.path.join(tmpdir, f"p{pn:04d}_l.pdf")
            _gs_crop(pn, f"[/CropBox [0 0 {sx} {h}] /PAGE pdfmark", lp, input_pdf)

            # B6a: Normalize left half (MediaBox = [0, 0, sx, h], delete CropBox)
            with Pdf.open(lp, allow_overwriting_input=True) as lp_pdf:
                lp_page = lp_pdf.pages[0]
                lp_page.MediaBox = pikepdf.Array([0, 0, sx, h])
                if "/CropBox" in lp_page:
                    del lp_page["/CropBox"]
                lp_pdf.save(lp)

            rp = os.path.join(tmpdir, f"p{pn:04d}_r.pdf")
            _gs_crop(pn, f"[/CropBox [{sx} 0 {w} {h}] /PAGE pdfmark", rp, input_pdf)

            # B6a: Normalize right half (shift by -sx, MediaBox = [0, 0, w - sx, h], delete CropBox)
            with Pdf.open(rp, allow_overwriting_input=True) as rp_pdf:
                rp_page = rp_pdf.pages[0]
                shift_ops = f"1 0 0 1 {-sx} 0 cm\n".encode("ascii")
                shift_stream = rp_pdf.make_stream(shift_ops)
                contents = rp_page.get("/Contents")
                if isinstance(contents, pikepdf.Array):
                    contents.insert(0, shift_stream)
                else:
                    new_array = pikepdf.Array()
                    new_array.append(shift_stream)
                    if contents is not None:
                        new_array.append(contents)
                    rp_page.Contents = new_array
                rp_page.MediaBox = pikepdf.Array([0, 0, w - sx, h])
                if "/CropBox" in rp_page:
                    del rp_page["/CropBox"]
                rp_pdf.save(rp)

            page_files.extend([lp, rp])
            page_map.append({"orig": pn, "type": "split", "side": "left",
                            "split_x": round(sx, 1), "w": round(w, 1), "h": round(h, 1)})
            page_map.append({"orig": pn, "type": "split", "side": "right",
                            "split_x": round(sx, 1), "w": round(w, 1), "h": round(h, 1)})
        else:
            fp = os.path.join(tmpdir, f"p{pn:04d}_full.pdf")
            cmd = [
                "gs", "-sDEVICE=pdfwrite", "-dNOPAUSE", "-dQUIET", "-dBATCH",
                f"-dFirstPage={pn}", f"-dLastPage={pn}",
                f"-sOutputFile={fp}", "-f", input_pdf,
            ]
            subprocess.run(cmd, capture_output=True, timeout=60)
            if not os.path.exists(fp):
                raise RuntimeError(f"Ghostscript copy failed for page {pn}")
            page_files.append(fp)
            page_map.append({"orig": pn, "type": "full",
                            "w": round(w, 1), "h": round(h, 1)})

    pdf.close()

    # Merge page files into output
    if len(page_files) == 1:
        shutil.copy2(page_files[0], output_pdf)
    else:
        subprocess.run(
            ["qpdf", "--empty", "--pages"] + page_files + ["--", output_pdf],
            capture_output=True, check=True
        )

    with open(map_path, "w") as f:
        json.dump({"pages": page_map, "source": input_pdf}, f, indent=2)

    sc = sum(1 for e in page_map if e["type"] == "split")
    print(f"   📐 Split: {total_pages} pages → {len(page_map)} "
          f"({sc//2} split, {total_pages - sc//2} full pages)", file=sys.stderr)

    shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
#  MERGE (pikepdf add_overlay)
# ═══════════════════════════════════════════════════════════════

def merge_pdf(input_pdf: str, output_pdf: str, map_path: str):
    """Reassemble half-page pairs into full pages."""
    import pikepdf
    from pikepdf import Pdf

    with open(map_path) as f:
        page_map = json.load(f)["pages"]

    src = Pdf.open(input_pdf)
    dst = Pdf.new()
    i = 0

    while i < len(page_map):
        entry = page_map[i]

        if entry["type"] == "full":
            dst.pages.append(src.pages[i])
            i += 1

        elif entry["type"] == "split" and entry["side"] == "left":
            if i + 1 >= len(page_map) or page_map[i + 1]["type"] != "split":
                dst.pages.append(src.pages[i])
                i += 1
                continue

            w, h = entry["w"], entry["h"]
            sx = entry["split_x"]

            full = dst.add_blank_page(page_size=(w, h))

            # Left half overlay (0, 0, split_x, h)
            full.add_overlay(src.pages[i],
                            pikepdf.Rectangle(0, 0, sx, h))

            # Right half overlay (split_x, 0, w, h)
            full.add_overlay(src.pages[i + 1],
                            pikepdf.Rectangle(sx, 0, w, h))

            i += 2

        else:
            dst.pages.append(src.pages[i])
            i += 1

    dst.save(output_pdf)
    print(f"   📐 Merge: {len(src.pages)} → {len(dst.pages)} pages "
          f"({len(src.pages) - len(dst.pages)} pairs reassembled)", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════
#  VERIFY (B4/B5 quality check helper)
# ═══════════════════════════════════════════════════════════════

def verify_ocr_split(pdf_path: str, map_path: str) -> bool:
    """Verify that no split page half lost its text while the other has text."""
    try:
        with open(map_path) as f:
            page_map = json.load(f)["pages"]
    except Exception:
        # No map, cannot verify split-specific loss
        return True

    # -raw for consistency with quality_check()'s reasoning in pdf-lib.sh —
    # this only compares per-side character counts, which is order-independent,
    # but keeps both call sites reading these files the same way.
    result = subprocess.run(
        ["pdftotext", "-raw", pdf_path, "-"],
        capture_output=True, text=True, timeout=30
    )
    pages_text = result.stdout.split("\x0c") # Formfeed split
    if pages_text and not pages_text[-1].strip():
        pages_text.pop()

    if len(pages_text) != len(page_map):
        print(f"   ⚠️  Verify Page Count Mismatch: PDF has {len(pages_text)} but map has {len(page_map)}", file=sys.stderr)
        return False

    i = 0
    while i < len(page_map):
        entry = page_map[i]
        if entry["type"] == "split" and entry["side"] == "left":
            if i + 1 < len(page_map) and page_map[i + 1]["type"] == "split":
                text_l = pages_text[i].strip()
                text_r = pages_text[i + 1].strip()
                len_l = len(text_l)
                len_r = len(text_r)

                # Heuristic: if one side has substantial text (> 150 chars)
                # and the other has almost none (< 15 chars), signal failure.
                if (len_l > 150 and len_r < 15) or (len_r > 150 and len_l < 15):
                    print(f"   ⚠️  Suspicious text loss: page {entry['orig']} (left chars: {len_l}, right chars: {len_r})", file=sys.stderr)
                    return False
                i += 2
                continue
        i += 1
    return True


def verify_all_pages(pdf_path: str, min_chars: int = 50, allow_pages: set = None) -> list:
    """Per-page character-count floor across the WHOLE document (not just
    split pairs — see verify_ocr_split for that narrower check).

    This is the B5 gate: before overwriting a raw/ source file with a
    freshly (re)processed result, every page must clear a minimum text
    density, or the caller should refuse to overwrite. A document-wide
    average (as used by quality_check() in pdf-lib.sh) can hide a single
    dead page (0 chars) inside an otherwise fine 50-page document — exactly
    what happened with the half-pages that lost their text layer entirely
    (B4) while the file's overall average still looked acceptable.

    Returns a list of (page_num, char_count) for pages below the floor,
    excluding page numbers in allow_pages (the "blank page exemption" for
    known legitimate low-text pages — cover sheets, pure diagrams). Empty
    list means the whole document passes.
    """
    allow_pages = allow_pages or set()
    result = subprocess.run(
        ["pdftotext", "-raw", pdf_path, "-"],
        capture_output=True, text=True, timeout=60
    )
    pages_text = result.stdout.split("\x0c")
    if pages_text and not pages_text[-1].strip():
        pages_text.pop()

    failing = []
    for i, txt in enumerate(pages_text, start=1):
        if i in allow_pages:
            continue
        n = len(txt.strip())
        if n < min_chars:
            failing.append((i, n))
    return failing


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def parse_pages(s: str, mx: int) -> set:
    """Parse '3,5-9,12' into set of ints."""
    r = set()
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            for p in range(int(lo), int(hi) + 1):
                if 1 <= p <= mx: r.add(p)
        else:
            p = int(part)
            if 1 <= p <= mx: r.add(p)
    return r


def main():
    p = argparse.ArgumentParser(description="Two-column PDF tools")
    s = p.add_subparsers(dest="cmd", required=True)

    d = s.add_parser("detect", help="Detect two-column pages")
    d.add_argument("pdf")

    sp = s.add_parser("split", help="Split pages via CropBox")
    sp.add_argument("input"); sp.add_argument("output")
    sp.add_argument("--map", required=True)
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--auto", action="store_true")
    g.add_argument("--pages", type=str)

    mp = s.add_parser("merge", help="Reassemble half-pages")
    mp.add_argument("input"); mp.add_argument("output")
    mp.add_argument("--map", required=True)

    vp = s.add_parser("verify", help="Verify OCR split page text layer integrity")
    vp.add_argument("input")
    vp.add_argument("--map", required=True)

    ap = s.add_parser("verify-pages",
                       help="Per-page character-count floor (B5 gate before overwriting raw/)")
    ap.add_argument("input")
    ap.add_argument("--min-chars", type=int, default=50)
    ap.add_argument("--allow-pages", type=str, default="",
                     help="Pages exempt from the floor, e.g. '1,5-7' (known cover/diagram pages)")

    args = p.parse_args()

    if args.cmd == "detect":
        for pn in detect_columns(args.pdf):
            print(pn)

    elif args.cmd == "split":
        from pikepdf import Pdf
        mx = len(Pdf.open(args.input).pages)
        if args.all:
            sps = set(range(1, mx + 1))
        elif args.pages:
            sps = parse_pages(args.pages, mx)
        else:
            sps = set(detect_columns(args.input))
        split_pdf_gs(args.input, args.output, args.map, sps,
                     detect_first=args.auto)

    elif args.cmd == "merge":
        merge_pdf(args.input, args.output, args.map)

    elif args.cmd == "verify":
        if not verify_ocr_split(args.input, args.map):
            sys.exit(1)
        sys.exit(0)

    elif args.cmd == "verify-pages":
        from pikepdf import Pdf
        mx = len(Pdf.open(args.input).pages)
        allow = parse_pages(args.allow_pages, mx) if args.allow_pages else set()
        failing = verify_all_pages(args.input, min_chars=args.min_chars, allow_pages=allow)
        if failing:
            for pn, n in failing:
                print(f"   🗑️  Page {pn}: only {n} characters (min: {args.min_chars})", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)


if __name__ == "__main__":
    main()


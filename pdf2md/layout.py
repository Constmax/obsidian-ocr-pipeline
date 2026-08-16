#!/usr/bin/env python3
"""Page geometry: columns, boxes, tables, diagrams (Stage 2, Issue #8).

Separated from pdf2md.py. Operates on fitz Page objects and is thus not
headless testable — pure functions on line lists reside in zusammenbau.py.
Imports only from zusammenbau (one direction, no cycle).
"""
import re
import statistics

from zusammenbau import ENUMERATION, NO_JOIN, is_boilerplate, clean_text


def _column_gap(with_box):
    """(Position of column gap, full-width lines) or None."""
    if len(with_box) < 8:
        return None
    type_set = [z for z in with_box if not is_boilerplate(z[0], z[1][1])] or with_box
    starts = sorted(z[1][0] for z in type_set)
    width = max(z[1][2] for z in type_set) - min(z[1][0] for z in type_set)
    if width <= 0:
        return None
    full = [z for z in with_box if z[1][2] - z[1][0] > width * 0.6]
    best = None
    for a, b in zip(starts, starts[1:]):
        if b <= a:
            continue
        gap, pos = b - a, (a + b) / 2
        n_left = sum(1 for z in with_box if z[1][0] < pos)
        ratio = min(n_left, len(with_box) - n_left) / len(with_box)
        if ratio < 0.25:
            continue
        crossings = sum(1 for z in with_box
                        if z not in full and z[1][0] < pos < z[1][2])
        clean = gap >= width * 0.08 and crossings <= 0.02 * len(with_box)
        if not (gap >= width * 0.25 or clean):
            continue
        rank = (-crossings, gap)
        if best is None or rank > best[0]:
            best = (rank, pos)
    if best is None:
        return None
    return best[1], full


def split_columns(lines, depth=0):
    """Single column → sorted by y. Two column → left column, then right column."""
    with_box = [z for z in lines if z[1]]
    y = lambda z: z[1][1]
    if depth >= 2:
        return sorted(lines, key=lambda z: z[1][1] if z[1] else 0)
    hit = _column_gap(with_box)
    if hit is None:
        return sorted(lines, key=lambda z: z[1][1] if z[1] else 0) \
            if depth or with_box else lines
    pos, full = hit
    left = [z for z in with_box if z not in full and z[1][0] < pos]
    right = [z for z in with_box if z not in full and z[1][0] >= pos]
    header = [z for z in full if y(z) < min([y(z) for z in left + right], default=0)]
    rest_full = [z for z in full if z not in header]

    if _column_gap(left) is None and _column_gap(right) is None:
        grid = question_answer_grid(left, right)
        if grid is not None:
            return sorted(header, key=y) + grid + sorted(rest_full, key=y)
    return (sorted(header, key=y) + split_columns(left, depth + 1)
            + split_columns(right, depth + 1) + sorted(rest_full, key=y))


def _is_line_start(text):
    """Does a new thought begin here? Heading or bold line."""
    bare = text.lstrip("*").lstrip()
    return bool(ENUMERATION.match(bare)) or (
        text.startswith("**") and text.endswith("**")
        and text.count("**") == 2 and len(bare) <= 90)


def _blocks(column, factor=0.8):
    """Cut column lines into blocks by whitespace."""
    column = sorted(column, key=lambda z: z[1][1])
    if not column:
        return []
    med = statistics.median([z[1][3] - z[1][1] for z in column]) or 10
    out = [[column[0]]]
    for a, b in zip(column, column[1:]):
        (out.append([b]) if b[1][1] - a[1][3] > factor * med
         else out[-1].append(b))
    return out


def question_answer_grid(left, right, tol=3):
    """Two columns as Markdown table if right depends on left."""
    left = [z for z in left if not is_boilerplate(clean_text(z[0]), z[1][1])]
    right = [z for z in right if not is_boilerplate(clean_text(z[0]), z[1][1])]
    if len(left) < 6 or len(right) < 6:
        return None
    starts = sorted(z[1][1] for z in left if _is_line_start(z[0]))
    if len(starts) < 4:
        return None
    right_blocks = _blocks(right)
    matching = sum(1 for b in right_blocks
                   if any(abs(b[0][1][1] - a) <= tol for a in starts))
    if matching < 3 or matching < 0.75 * len(right_blocks):
        return None

    sorted_left = sorted(left, key=lambda z: z[1][1])
    rows, preamble = [], []
    for z in sorted_left:
        if _is_line_start(z[0]):
            rows.append([z])
        elif rows:
            rows[-1].append(z)
        else:
            preamble.append(z)

    def text(group):
        s = ""
        for z in group:
            t = clean_text(z[0])
            if s.endswith("-") and not NO_JOIN.match(t) and t[:1].islower():
                s = s[:-1] + t
            else:
                s = (s + " " + t) if s else t
        s = re.sub(r"\*\*(\s*)\*\*", r"\1", s).strip()
        return re.sub(r"\s{2,}", " ", s).replace("|", r"\|")

    bounds = [r[0][1][1] for r in rows] + [10 ** 6]
    out = [[clean_text(z[0]), z[1]] for z in preamble]
    for z in sorted(right, key=lambda q: q[1][1]):
        if z[1][1] < bounds[0] - tol:
            out.append([clean_text(z[0]), z[1]])

    questions = sum(1 for r in rows if r[-1][0].rstrip().endswith("?"))
    title = ("| Frage | Antwort |" if rows and questions >= 0.5 * len(rows)
             else "|  |  |")
    buffer = []

    def close_table(box):
        if not buffer:
            return
        out.append(["\n".join([title, "| --- | --- |"] + buffer),
                    box, "tabelle"])
        buffer.clear()

    for i, row in enumerate(rows):
        top, bottom = bounds[i] - tol, bounds[i + 1] - tol
        answer = [z for z in sorted(right, key=lambda q: q[1][1])
                  if top <= z[1][1] < bottom]
        box = (min(z[1][0] for z in row), row[0][1][1],
               max(z[1][2] for z in row + answer),
               max(z[1][3] for z in row + answer))
        question = text(row)
        if not answer and _is_line_start(row[0][0]) and len(row) == 1 \
                and row[0][0].startswith("**"):
            close_table(box)
            out.append([question, box])
            continue
        buffer.append(f"| {question} | {text(answer)} |")
    close_table((0, bounds[-2] if len(bounds) > 1 else 0, 1000, 1000))
    return out


# --- Page Profile & Layout Detection ---------------------------------------

def _ink_gutter(page, dpi=72, quantile=60, trough=0.35, flank=0.35,
               min_width=0.015, gap=0.01):
    """(gutter_relative, gutter_width_relative) from vertical ink profile."""
    import fitz
    import numpy as np
    pm = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    arr = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width)
    ink = (arr < 200).sum(axis=0).astype(float)
    W = pm.width
    ref = float(np.percentile(ink[int(W * 0.10):int(W * 0.90)], quantile))
    if ref <= 0:
        return None, 0.0
    lo, hi, threshold = int(W * 0.30), int(W * 0.70), ref * trough
    window = max(1, int(W * 0.05))

    troughs, current = [], None
    for i in range(lo, hi + 1):
        if i < hi and ink[i] < threshold:
            current = i if current is None else current
        elif current is not None:
            troughs.append([current, i])
            current = None
    merged = []
    for t in troughs:
        if merged and t[0] - merged[-1][1] <= W * gap:
            merged[-1][1] = t[1]
        else:
            merged.append(t)

    best = (0, None)
    for a, b in merged:
        left, right = ink[max(0, a - window):a], ink[b:b + window]
        if (b - a) / W < min_width or not len(left) or not len(right):
            continue
        if (float(np.median(left)) < ref * flank
                or float(np.median(right)) < ref * flank):
            continue
        if b - a > best[0]:
            best = (b - a, (a + b) // 2)
    width, center = best
    return (center / W if center is not None else None), width / W


def _edge_ratio(page, gutter):
    """Ratio of text lines whose right edge ends at the gutter."""
    W = page.rect.width or 1
    edges = [ln["bbox"][2] / W for b in page.get_text("dict")["blocks"]
             if b.get("type") == 0 for ln in b["lines"]]
    if len(edges) < 20:
        return None
    lo, hi = gutter - 0.15, gutter + 0.10
    return sum(1 for x in edges if lo <= x <= hi) / len(edges)


def detect_layout(page):
    """('einspaltig'|'zweispaltig', gutter_relative)."""
    gutter, width = _ink_gutter(page)
    if gutter is None:
        return "einspaltig", None
    ratio = _edge_ratio(page, gutter)
    is_two = ratio >= 0.30 if ratio is not None else width >= 0.010
    return ("zweispaltig", gutter) if is_two else ("einspaltig", None)


def image_ratio(page):
    """Area ratio of embedded raster images."""
    import fitz
    total = abs(page.rect.width * page.rect.height) or 1
    area = 0.0
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") == 1:
            x0, y0, x1, y1 = b["bbox"]
            area += abs((x1 - x0) * (y1 - y0))
    return min(area / total, 1.0)


# --- Boxes & Diagrams ------------------------------------------------------

def _deduplicate(boxes, close=6):
    """Merge nearly identical rectangles."""
    out = []
    for k in sorted(boxes, key=lambda r: -(r[2] - r[0]) * (r[3] - r[1])):
        if not any(all(abs(k[i] - v[i]) <= close for i in range(4)) for v in out):
            out.append(k)
    return out


def _longest_run(mask):
    """Length of longest contiguous True run per row."""
    import numpy as np
    run = np.zeros(mask.shape[0], dtype=np.int32)
    current = np.zeros(mask.shape[0], dtype=np.int32)
    for j in range(mask.shape[1]):
        current = np.where(mask[:, j], current + 1, 0)
        np.maximum(run, current, out=run)
    return run


def _merge_boxes(boxes, x_tol=4, y_gap=5):
    """Merge vertically contiguous rectangles of equal width."""
    open_boxes = sorted(boxes, key=lambda k: (round(k[0]), round(k[2]), k[1]))
    out = []
    for k in open_boxes:
        if out and abs(out[-1][0] - k[0]) <= x_tol and abs(out[-1][2] - k[2]) <= x_tol \
                and k[1] - out[-1][3] <= y_gap:
            v = out[-1]
            out[-1] = (v[0], min(v[1], k[1]), v[2], max(v[3], k[3]))
        else:
            out.append(k)
    return out


def vector_boxes(page, min_w=25, min_h=14):
    return _merge_boxes(_deduplicate(
        [(d["rect"].x0, d["rect"].y0, d["rect"].x1, d["rect"].y1)
         for d in page.get_drawings()
         if d["rect"].width >= min_w and d["rect"].height >= min_h]))


def raster_boxes(page, dpi=110, transverse=0.12, longitudinal=0.12, thickness=4):
    """Boxes from continuous straight lines in image."""
    import fitz
    import numpy as np
    pm = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    a = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width)
    dark = a < 170
    H, W = dark.shape

    def collect(run, length, margin, minimum):
        tr = np.where(run >= length * minimum)[0]
        tr = tr[(tr > margin) & (tr < len(run) - margin)]
        out, block = [], []
        for i in tr:
            if block and i - block[-1] > thickness:
                out.append(sum(block) / len(block)); block = []
            block.append(i)
        if block:
            out.append(sum(block) / len(block))
        return out

    ys = collect(_longest_run(dark), W, int(H * 0.02), transverse)
    xs = collect(_longest_run(dark.T), H, int(W * 0.04), longitudinal)

    sx, sy = page.rect.width / W, page.rect.height / H
    out = [(x0 * sx, y0 * sy, x1 * sx, y1 * sy)
           for y0, y1 in zip(ys, ys[1:]) if (y1 - y0) >= H * 0.02
           for x0, x1 in zip(xs, xs[1:]) if (x1 - x0) >= W * 0.08]
    return _deduplicate(out)


def _cluster(values, tol):
    values = sorted(values)
    n = 1
    for a, b in zip(values, values[1:]):
        if b - a > tol:
            n += 1
    return n


def slanted_lines(page, min_len=10):
    """Drawing commands with at least one true diagonal line — arrows."""
    n = 0
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                if (abs(it[2].x - it[1].x) > min_len
                        and abs(it[2].y - it[1].y) > min_len):
                    n += 1
                    break
    return n


def is_diagram(boxes):
    """Decide based solely on spread of box widths."""
    if len(boxes) < 3:
        return False
    widths = [(k[2] - k[0]) / 1000 for k in boxes]
    if _cluster(widths, 0.05) < 3:
        return False
    m = sum(widths) / len(widths)
    return (sum((b - m) ** 2 for b in widths) / len(widths)) ** 0.5 >= 0.05


def side_by_side(boxes, margin=8):
    """Are there two boxes at same height without x-overlap?"""
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            height = min(a[3], b[3]) - max(a[1], b[1])
            if height <= 0.4 * min(a[3] - a[1], b[3] - b[1]):
                continue
            if a[2] <= b[0] + margin or b[2] <= a[0] + margin:
                return True
    return False


def detect_boxes(page, scan, table_frames=()):
    """(Boxes in 0–1000, is_diagram). Only boxes WITH text count."""
    W = page.rect.width or 1
    H = page.rect.height or 1
    lines = [ln["bbox"] for b in page.get_text("dict")["blocks"]
             if b.get("type") == 0 for ln in b["lines"]
             if "".join(s["text"] for s in ln["spans"]).strip()]
    area = abs(W * H) or 1
    with_text = []
    for k in (raster_boxes(page) if scan else vector_boxes(page)):
        if (k[2] - k[0]) * (k[3] - k[1]) >= 0.75 * area:
            continue
        if (k[3] - k[1]) >= 0.85 * H or (k[2] - k[0]) >= 0.92 * W:
            continue
        mx, my = (k[0] + k[2]) / 2, (k[1] + k[3]) / 2
        if any(x0 <= mx <= x1 and y0 <= my <= y1 for x0, y0, x1, y1 in table_frames):
            continue
        n = sum(1 for z in lines
                if k[0] <= (z[0] + z[2]) / 2 <= k[2]
                and k[1] <= (z[1] + z[3]) / 2 <= k[3])
        if n:
            with_text.append((k, n))
    norm = lambda k: (int(k[0] / W * 1000), int(k[1] / H * 1000),
                      int(k[2] / W * 1000), int(k[3] / H * 1000))
    all_boxes = [norm(k) for k, _ in with_text]
    multiline = [norm(k) for k, n in with_text if n >= 2]
    diag = (is_diagram(all_boxes)
            or (not scan and slanted_lines(page) >= 2)
            or (not scan and side_by_side(multiline)))
    return [norm(k) for k, n in with_text if n >= 2], diag


def assign_boxes(lines, boxes, x_range=None):
    """Attach to each line the box in which it lies."""
    if not boxes:
        return lines
    for z in lines:
        if not z[1] or (len(z) > 2 and z[2] == "tabelle"):
            continue
        mx, my = (z[1][0] + z[1][2]) / 2, (z[1][1] + z[1][3]) / 2
        for i, k in enumerate(boxes):
            if x_range is not None:
                if not (k[0] < x_range[1] and k[2] > x_range[0]):
                    continue
                hit = k[1] <= my <= k[3]
            else:
                hit = k[0] <= mx <= k[2] and k[1] <= my <= k[3]
            if hit:
                while len(z) < 3:
                    z.append(None)
                z[2] = f"kasten{i}"
                break
    return lines


def _clean_cell(t):
    """Sanitize cell content for a Markdown table."""
    t = (t or "").strip()
    t = re.sub(r"(?<=[a-zäöüß])-\s*\n\s*(?=[a-zäöüß])", "", t)
    t = re.sub(r"\s*\n\s*", " ", t)
    t = t.replace("|", "\\|")
    return re.sub(r"\s+", " ", t)


def tables_markdown(page):
    """[(y_top, markdown, bbox)] for each lined table of the page."""
    import fitz
    H = page.rect.height or 1
    out = []
    try:
        tabs = page.find_tables(strategy="lines_strict").tables
    except Exception:
        return out
    for t in tabs:
        if t.row_count < 2 or t.col_count < 2:
            continue
        rows = [[_clean_cell(c) for c in r] for r in t.extract()]
        if not rows:
            continue
        columns = [j for j in range(max(len(r) for r in rows))
                   if any(j < len(r) and r[j] for r in rows)]
        row_indices = [i for i, r in enumerate(rows) if any(r)]
        rows = [[rows[i][j] if j < len(rows[i]) else "" for j in columns]
                for i in row_indices]
        if (len(rows) < 2 or len(rows[0]) < 2
                or sum(1 for r in rows if sum(1 for c in r if c) >= 2) < 2):
            continue

        for zi, i in enumerate(row_indices):
            if i >= len(t.rows):
                break
            cells = t.rows[i].cells or []
            for sj, j in enumerate(columns):
                if j >= len(cells) or not cells[j] or not rows[zi][sj]:
                    continue
                spans = [s for b in page.get_text("dict",
                                                  clip=fitz.Rect(cells[j]))["blocks"]
                         if b.get("type") == 0
                         for ln in b["lines"] for s in ln["spans"] if s["text"].strip()]
                if spans and all(s.get("flags", 0) & 16
                                 or "bold" in s.get("font", "").lower() for s in spans):
                    rows[zi][sj] = f"**{rows[zi][sj]}**"
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        header = rows[0]
        if not any(header):
            header = [""] * width
            body = rows
        else:
            body = rows[1:]
        md = ["| " + " | ".join(header) + " |",
              "|" + "|".join([" --- "] * width) + "|"]
        md += ["| " + " | ".join(r) for r in body]
        out.append((t.bbox[1] / H, "\n".join(md), t.bbox))
    return out

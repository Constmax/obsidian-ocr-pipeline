#!/usr/bin/env python3
"""Tiling, model execution, derailment detection and repair (Stage 2, Issue #8).

Separated from pdf2md.py. Text functions (loop_length, is_derailed,
trim_overlap, …) are testable without model because fitz/numpy/PIL/
mlx_vlm are imported function-locally. Imports only from assembly.
"""
import math
import re
import statistics
from collections import Counter

from assembly import parse_lines

OVERLAP = 0.02
# --- Rendering & Inference -------------------------------------------------


def detect_bold(lines, image_path, factor=1.45, max_width=0.75):
    """Detect bold text line-by-line via ink density and add `**`."""
    import numpy as np
    from PIL import Image
    with_box = [z for z in lines if z[1]]
    if len(with_box) < 5:
        return lines
    g = np.asarray(Image.open(image_path).convert("L"))
    H, W = g.shape

    values = []
    for z in with_box:
        x0, y0, x1, y1 = z[1]
        px0, py0 = int(x0 / 1000 * W), int(y0 / 1000 * H)
        px1, py1 = int(x1 / 1000 * W), int(y1 / 1000 * H)
        if px1 - px0 < 5 or py1 - py0 < 3:
            values.append(None)
            continue
        field = g[py0:py1, px0:px1]
        values.append(((field < 128).mean(), py1 - py0))

    valid = [w for w in values if w]
    if len(valid) < 5:
        return lines

    from collections import defaultdict
    classes = defaultdict(list)
    for density, height in valid:
        classes[height // 5].append(density)
    global_med = statistics.median(d for d, _ in valid)

    column_width = max((z[1][2] - z[1][0]) for z in with_box) or 1

    for z, w in zip(with_box, values):
        if not w:
            continue
        density, height = w
        group = classes.get(height // 5, [])
        med = statistics.median(group) if len(group) >= 3 else global_med
        wide = (z[1][2] - z[1][0]) / column_width
        if med > 0 and density > med * factor and wide <= max_width:
            t = z[0].strip()
            if t and not t.startswith("**"):
                z[0] = f"**{t}**"
    return lines


def tile_vertically(png, gutter):
    """Split two-column page at detected gutter."""
    from PIL import Image
    im = Image.open(png)
    w, h = im.size
    cut, ov = int(w * gutter), int(w * OVERLAP)
    a = png.with_name(png.stem + "_L.png")
    b = png.with_name(png.stem + "_R.png")
    im.crop((0, 0, min(cut + ov, w), h)).save(a)
    im.crop((max(cut - ov, 0), 0, w, h)).save(b)
    return [a, b]


def tile_horizontally(png, parts=2):
    """Split dense single-column page top/bottom."""
    from PIL import Image
    im = Image.open(png)
    w, h = im.size
    ov = int(h * OVERLAP)
    out = []
    for i in range(parts):
        y0 = max(int(h * i / parts) - ov, 0)
        y1 = min(int(h * (i + 1) / parts) + ov, h)
        p = png.with_name(f"{png.stem}_T{i+1}.png")
        im.crop((0, y0, w, y1)).save(p)
        out.append((p, y0 / h, y1 / h))
    return out


# --- Derailed Generation ----------------------------------------------------

LOOP_THRESHOLD = 8
COUNTER_THRESHOLD = 20
CHARACTERS_PER_INK = 18.8 / 1000
CALIBRATED_CORRIDOR = (0.80, 2.2)
COARSE_CORRIDOR = (0.45, 2.6)

TOKENS_PER_CHAR = 1 / 2.2
TOKEN_MIN, TOKEN_MAX = 1024, 8192


def _token_budget(expected, generous=1.8):
    if not expected or expected < 300:
        return TOKEN_MAX
    return int(min(max(expected * TOKENS_PER_CHAR * generous, TOKEN_MIN),
                   TOKEN_MAX))


def _ink_amount(png, dpi=150):
    """Ink pixels of image, normalized to 150 dpi."""
    import numpy as np
    from PIL import Image
    g = np.asarray(Image.open(png).convert("L"))
    return float((g < 160).sum()) * (150.0 / max(dpi, 1)) ** 2


def loop_length(text, n=5):
    """How often does the most frequent n-gram occur?"""
    w = [re.sub(r"\d+", "#", x) for x in re.findall(r"\S+", text)]
    if len(w) < 2 * n:
        return 0
    frequent = Counter(tuple(w[i:i + n]) for i in range(len(w) - n + 1))
    return frequent.most_common(1)[0][1]


def is_derailed(text, expected=None, calibrated=False):
    """(Reason, metric) — or (None, 0.0) if output is plausible."""
    s = loop_length(text)
    if s >= LOOP_THRESHOLD:
        return "Schleife", float(s)
    if expected and expected >= 300:
        low, high = CALIBRATED_CORRIDOR if calibrated else COARSE_CORRIDOR
        q = len(text) / expected
        if q > high:
            return "zu lang", q
        if q < low:
            return "Abbruch", q
    return None, 0.0


def _quality(text, expected):
    """How credible is this output? Smaller is better."""
    penalty = 10.0 if loop_length(text) >= LOOP_THRESHOLD else 0.0
    if expected and expected >= 300:
        return penalty + abs(math.log(max(len(text), 1) / expected))
    return penalty - min(len(text), 20000) / 20000.0


def _trim_run(items, minimum, keep=2, key=None):
    """Trim consecutive repetitions of a period."""
    k = items if key is None else key
    out, i = [], 0
    while i < len(items):
        for p in range(1, 5):
            if i + p > len(items):
                continue
            n = 1
            while k[i + n * p:i + (n + 1) * p] == k[i:i + p]:
                n += 1
            if n >= minimum:
                out += items[i:i + keep * p]
                i += n * p
                break
        else:
            out.append(items[i])
            i += 1
    return out


def trim_loop(lines, minimum=3):
    """Trim remaining repetitions."""
    trimmed = []
    for z in lines:
        w = z[0].split(" ")
        if len(w) >= 3 * minimum:
            new_w = _trim_run(w, minimum)
            if len(new_w) >= 3 * COUNTER_THRESHOLD:
                new_w = _trim_run(
                    new_w, COUNTER_THRESHOLD,
                    key=[re.sub(r"\d+", "#", x) for x in new_w])
            new_w = " ".join(new_w)
            if new_w != z[0]:
                z = [new_w] + list(z[1:])
        trimmed.append(z)
    key_list = [re.sub(r"[^0-9a-zäöüß]+", "", z[0].lower()) for z in trimmed]
    kept, i = [], 0
    while i < len(trimmed):
        n = 1
        while (i + n < len(trimmed) and key_list[i + n] == key_list[i]
               and len(key_list[i]) >= 8):
            n += 1
        kept += trimmed[i:i + (2 if n >= minimum else n)]
        i += n
    return kept


def _seam_words(lines):
    out = []
    for i, z in enumerate(lines):
        for j, w in enumerate(z[0].split()):
            k = re.sub(r"[^0-9a-zäöüß]+", "", w.lower())
            if k:
                out.append((k, i, j))
    return out


def trim_overlap(existing, new_lines, window=150, minimum=6):
    """Cut duplicate text at tile seam — word by word."""
    if not existing or not new_lines:
        return new_lines
    tail = [w for w, _, _ in _seam_words(existing)][-window:]
    head = _seam_words(new_lines)[:window]
    head_words = [w for w, _, _ in head]
    for k in range(min(len(tail), len(head_words)), minimum - 1, -1):
        if tail[-k:] != head_words[:k]:
            continue
        if sum(len(w) for w in head_words[:k]) < 30:
            continue
        _, line_idx, word_idx = head[k - 1]
        out = []
        for i, z in enumerate(new_lines):
            if i < line_idx:
                continue
            if i == line_idx:
                rest = " ".join(z[0].split()[word_idx + 1:])
                if not rest:
                    continue
                z = [rest] + list(z[1:])
            out.append(z)
        return out
    return new_lines


def tile_lines(png, ocr, with_bold, factor, dpi, calibrated=False,
               depth=0, max_depth=1):
    """Process a tile — and recalculate on derailed generation."""
    expected = _ink_amount(png, dpi) * factor if factor else None
    lines = parse_lines(ocr(png, _token_budget(expected)))
    if with_bold:
        lines = detect_bold(lines, png)
    text = "\n".join(z[0] for z in lines)
    reason, metric = is_derailed(text, expected, calibrated)
    if reason is None:
        return lines, []
    mark = (f"{reason} {metric:.0f}×" if reason == "Schleife"
            else f"{reason} {metric:.0%}")
    if depth >= max_depth:
        if reason == "Schleife":
            before = len(lines)
            lines = trim_loop(lines)
            return lines, [f"{png.stem}: {mark}, not resolved — "
                           f"repetition trimmed ({before} → "
                           f"{len(lines)} lines)"]
        return lines, [f"{png.stem}: {mark}, not resolved"]
    new_lines, trace = [], []
    for part, top, bottom in tile_horizontally(png, 2):
        z, s = tile_lines(part, ocr, with_bold, factor, dpi, calibrated,
                          depth + 1, max_depth)
        trace += s
        height = bottom - top
        for e in z:
            if e[1]:
                e[1] = (e[1][0], int((top + e[1][1] / 1000 * height) * 1000),
                        e[1][2], int((top + e[1][3] / 1000 * height) * 1000))
        new_lines += trim_overlap(new_lines, z)
    new_text = "\n".join(e[0] for e in new_lines)
    if _quality(new_text, expected) < _quality(text, expected):
        chosen, note = new_lines, (f"{mark} → retiled, "
                                   f"{len(text)} → {len(new_text)} chars")
    else:
        chosen, note = lines, (f"{mark} → retry discarded "
                               f"({len(new_text)} chars were not better)")
    if loop_length("\n".join(e[0] for e in chosen)) >= LOOP_THRESHOLD:
        before = len(chosen)
        chosen = trim_loop(chosen)
        note += f"; repetition trimmed ({before} → {len(chosen)} lines)"
    return chosen, trace + [f"{png.stem}: {note}"]

#!/usr/bin/env python3
"""Markdown assembly: pure functions operating on line lists (Stage 2, Issue #8).

Separated from pdf2md.py so that the layer changing most frequently can be tested
without MLX, fitz, and Vault assets (pdf2md/test). Imports nothing from sibling
modules — dependency flows only in this direction:

    pdf2md.py (CLI)  →  layout.py, ocr.py  →  zusammenbau.py

Heavy imports (fitz, numpy, PIL) are function-local in all modules.
"""
import json
import re
import statistics

LOC = re.compile(r"<\|LOC_(\d+)\|>")
# --- Post-processing -------------------------------------------------------

# Words that must NOT be joined after a line-end hyphen.
NO_JOIN = re.compile(r"^(und|oder|bzw|sowie|als|wie|bis|von|zu|im|in)\b", re.I)

# --- Hemmer Boilerplate ----------------------------------------------------

CITIES = ("Augsburg Bayreuth Berlin Potsdam Bielefeld Bochum Bonn Bremen "
          "Düsseldorf Erlangen Frankfurt Freiburg Göttingen Greifswald Halle "
          "Hamburg Hannover Heidelberg Jena Kiel Köln Konstanz Leipzig "
          "Lüneburg Mainz Mannheim Marburg Gießen München Münster Nürnberg "
          "Osnabrück Passau Regensburg Saarbrücken Trier Tübingen Stuttgart "
          "Wiesbaden Würzburg Rostock Dresden").split()

CITY_FRAGMENT = re.compile(
    r"^(?:" + "|".join(CITIES) + r")\s*[-–]\s*"
    r"(?:[A-ZÄÖÜ][A-Za-zäöüß]{0,12}\.?)?$")

BOILERPLATE = [
    re.compile(r"^Juristisches\s+Repetitorium"),
    re.compile(r"^hemmer\s*$", re.I),
    re.compile(r"^Hauptkurs\s*/"),
    re.compile(r"^h\s*/\s*w\s*/\s*t\b"),                  # Footer
    re.compile(r"^\\lambda\s*/\s*\\omega"),               # Same, misread as LaTeX
    re.compile(r"^[–\-—]\s*\d+\s*[–\-—]?\s*$"),           # "– 1 –"
    re.compile(r"^\d{1,3}\s*[-–]\s*[Il1]\s*$"),           # "26-I"
]

# Weak signals that count as boilerplate only in header/footer zones.
ZONE_SIGNALS = [
    re.compile(r"^he[mn]+er\s*[.,:]?$", re.I),
    re.compile(r"^\W*(Juristisches\s*)?Repetitorium\W*$", re.I),
    re.compile(r"^(BGB|StGB|StR|ZR|OeR|ÖR)[\s-]*(AT|BT)?\s*$"),
    re.compile(r"(Lösung|Sachverhalte?|Übersicht)\s*[-–]\s*Seite", re.I),
    re.compile(r"^Fall\s*\d*\s*[-–]?\s*L[äöa]?"),      # "Fall 3 - Lä" (truncated)
    re.compile(r"^\s*Seite\s*\d+\s*$", re.I),
]

# Filled per document from set_running().
RUNNING = set()


def set_running(lines):
    """Set running header/footer lines of the document."""
    RUNNING.clear()
    RUNNING.update(lines)


def is_boilerplate(text, y=None, header_zone=70, footer_zone=950):
    """Detect Hemmer boilerplate."""
    t = text.strip().strip("*").strip()
    if not t:
        return False
    if RUNNING and re.sub(r"\s+", " ", re.sub(r"\*", "", t)).strip() in RUNNING:
        return True
    if any(p.search(t) for p in BOILERPLATE):
        return True
    if t.count(" - ") >= 2 and sum(1 for s in CITIES if s in t) >= 2:
        return True
    if CITY_FRAGMENT.match(t):
        return True

    if len(t) <= 45 and any(p.search(t) for p in ZONE_SIGNALS):
        return True

    if (y is not None and (y <= 80 or y >= 905)
            and re.fullmatch(r"\d{1,4}", t)):
        return True

    in_zone = y is not None and (y <= header_zone or y >= footer_zone)
    if in_zone:
        if any(p.search(t) for p in ZONE_SIGNALS):
            return True
        if len(t) <= 40 and sum(1 for s in CITIES if s in t) >= 1 and "-" in t:
            return True
    return False


ARROWS = {"rightarrow": "→", "Rightarrow": "⇒", "leftarrow": "←",
          "Leftarrow": "⇐", "leftrightarrow": "↔", "Leftrightarrow": "⇔",
          "downarrow": "↓", "Downarrow": "⇩", "uparrow": "↑", "to": "→",
          "Longrightarrow": "⟹", "mapsto": "↦"}

LATEX = [
    (re.compile(r"\$?\\(" + "|".join(ARROWS) + r")\$?(?![A-Za-z])"),
     lambda m: ARROWS[m.group(1)]),
    (re.compile(r"\\\(\\underline\{\\text\{(.*?)\}\}\\\)"), r"\1"),
    (re.compile(r"\\underline\{(.*?)\}"), r"\1"),
    (re.compile(r"\\text\{(.*?)\}"), r"\1"),
    (re.compile(r"\\\(|\\\)"), ""),
    (re.compile(r"\^\{(\d{1,2})\}"), r"[^\1]"),      # Footnote mark → Obsidian
    (re.compile(r"\$\^\{(\d{1,2})\}\$"), r"[^\1]"),
]

FN_START = r"[A-ZÄÖÜ„»§(]"
FN_DEF = re.compile(r"^(\d{1,2})\s+(?=" + FN_START + r")(.+)$")


def footnotes_obsidian(paragraphs):
    """Convert footnotes to Obsidian syntax: [^n] in text, [^n]: at block end."""
    defs, rest = {}, []
    is_table = lambda p: p.lstrip().startswith("|")
    for p in paragraphs:
        if is_table(p):
            rest.append(p)
            continue
        p = re.sub(r"\*\*(.+?)\*\*", r"\1", p) if FN_DEF.match(p.strip("* ")) else p
        m = FN_DEF.match(p.strip())
        if m and int(m.group(1)) <= 99:
            parts = re.split(r"(?<=[.\s])(?=\d{1,2}\s+" + FN_START + ")", p.strip())
            detected = False
            for part in parts:
                mm = FN_DEF.match(part.strip())
                if mm:
                    k = int(mm.group(1))
                    defs[k] = (defs[k] + " " if k in defs else "") \
                        + mm.group(2).strip()
                    detected = True
            if detected:
                continue
        rest.append(p)

    if not defs:
        return rest

    CITATION_BEFORE = re.compile(
        r"(§+|Art\.|Abs\.|S\.|Satz|Alt\.|Nr\.|Rn\.|Rz\.|Hs\.|Halbs\.|Var\.|"
        r"lit\.|Buchst\.|Seite|Fall|Teil|Rspr\.|Anm\.)\s*$")
    nums = sorted(defs)

    def mark(s):
        for n in nums:
            for m in re.finditer(rf"(?<=[a-zäöüßA-ZÄÖÜ)\].,;:]){n}(?![\d\]])", s):
                if CITATION_BEFORE.search(s[:m.start()]):
                    continue
                s = s[:m.start()] + f"[^{n}]" + s[m.end():]
                break
        return s
    rest = [p if is_table(p) else mark(p) for p in rest]
    rest += [""] + [f"[^{n}]: {defs[n]}" for n in nums]
    return rest


PUA = {
    "\uf0f0": "\u21e8",   # Wingdings 0xF0  Right shadow arrow
    "\uf0e0": "\u21e8",   # Wingdings 0xE0  Right block arrow
    "\uf0d8": "\u27a2",   # Wingdings 0xD8  Arrowhead bullet
    "\uf0fc": "\u2714",   # Wingdings 0xFC  Checkmark
    "\uf0b7": "\u2022",   # Symbol    0xB7  Bullet
    "\uf020": " ",        # Symbol    0x20  Space
}
PUA_FROM, PUA_TO = "\ue000", "\uf8ff"


def strip_pua(s):
    if not any(PUA_FROM <= c <= PUA_TO for c in s):
        return s
    return "".join(PUA.get(c, "\u25aa") if PUA_FROM <= c <= PUA_TO else c
                   for c in s)


def clean_text(s):
    s = strip_pua(s)
    for pat, rep in LATEX:
        s = pat.sub(rep, s)
    s = re.sub(r"\$\s*(?=\d)", "§ ", s)
    s = re.sub(r"§\s*§\s*", "§§ ", s)                 # "§ § 929" → "§§ 929"
    s = re.sub(r"§\s+(?=\d)", "§ ", s)
    s = re.sub(r"(§+\s*\d+[a-z]?\s*)\|", r"\1I", s)
    s = re.sub(r"\|\s+(?=(BGB|StGB|GG|VwGO|VwVfG|ZPO|HGB)\b)", "I ", s)
    s = re.sub(r"\*\*(\s*)\*\*", r"\1", s)
    return s.rstrip()


def parse_lines(text):
    """['text', (x_min, y_min, x_max, y_max)] per output line."""
    lines = []
    for raw in text.splitlines():
        coords = [int(m) for m in LOC.findall(raw)]
        plain = LOC.sub("", raw).strip()
        if not plain:
            continue
        if len(coords) >= 8:
            xs, ys = coords[0::2], coords[1::2]
            box = (min(xs), min(ys), max(xs), max(ys))
        else:
            box = None
        lines.append([plain, box])
    return lines


ENUMERATION = re.compile(
    r"^\s*([-•·▪○●⇒⇨→➢✔]"
    r"|\(?\d{1,2}[.)]"
    r"|[a-z]{1,3}[.)]"
    r"|[IVXL]{1,5}\.)(?=\s|$)"
)
KEYWORD_WORDS = (r"Anmerkung|Hinweis|Merksatz|Merke|Ergebnis|Beachte|"
                 r"Achtung|Exkurs|Vertiefung|Klausurtipp|"
                 r"Zwischenergebnis|Beispiele|Beispiel|Definition")
KEYWORD = re.compile(r"^(" + KEYWORD_WORDS + r")\s*:", re.I)
MARGIN_LABEL = re.compile(r"^\**\s*(?:" + KEYWORD_WORDS
                          + r")\s*:?\s*\**$", re.I)
LEVELS = (
    (re.compile(r"^\((?:\d{1,2}|[a-h]{1,2})\)(?=\s)"), 6),
    (re.compile(r"^(?:(?:aa|bb|cc|dd|ee|gg|hh)[.)]|ff\))(?=\s)"), 6),
    (re.compile(r"^(?:[a-eg-h][.)]|f\))(?=\s)"), 5),
    (re.compile(r"^\d{1,2}[.)](?=\s)"), 4),
    (re.compile(r"^[IVX]{1,5}\.(?=\s)"), 3),
    (re.compile(r"^[A-H][.)](?=\s)"), 2),
)
ABBREVIATION = re.compile(r"^[A-Za-zÄÖÜ]{1,2}\.")


def level(text):
    """Outline level (2–6) or None."""
    bare = re.sub(r"\*+", "", text).lstrip()
    for pat, lvl in LEVELS:
        m = pat.match(bare)
        if m:
            return None if ABBREVIATION.match(bare[m.end():].lstrip()) else lvl
    return None


def without_bold(text):
    return re.sub(r"\*\*", "", text).strip()


def only_bold(text):
    """Does paragraph consist exclusively of bold spans?"""
    return bool(text.strip()) and not re.sub(r"\*\*.*?\*\*", "", text,
                                             flags=re.S).strip()


def balance_bold(text):
    """Fix odd count of `**`."""
    if text.count("**") % 2 == 0:
        return text
    i = text.rfind("**")
    return text[:i] + text[i + 2:]


FN_NUMBER = re.compile(r"^\**\s*(\d{1,2})\s*\**$")
FN_TEXT = re.compile(r"^[A-ZÄÖÜ„»§]")


def attach_footnote_numbers(lines, footer=900, proximity=40):
    """Attach out-dented footnote number at page footer with its text."""
    out, i = [], 0
    while i < len(lines):
        z = lines[i]
        n = lines[i + 1] if i + 1 < len(lines) else None
        m = FN_NUMBER.match(z[0].strip())
        if (m and n and z[1] and n[1] and z[1][1] >= footer
                and z[1][0] <= n[1][0]
                and abs(n[1][1] - z[1][1]) <= proximity
                and FN_TEXT.match(n[0].lstrip("*").lstrip())):
            box = (min(z[1][0], n[1][0]), min(z[1][1], n[1][1]),
                   max(z[1][2], n[1][2]), max(z[1][3], n[1][3]))
            out.append([f"{m.group(1)} {n[0].lstrip()}", box] + list(n[2:]))
            i += 2
            continue
        out.append(z)
        i += 1
    return out


def is_heading(text, bare):
    """Is this short, entirely bold line a heading?"""
    return (text.startswith("**") and text.endswith("**")
            and text.count("**") == 2 and len(bare) <= 90
            and not MARGIN_LABEL.match(text.strip()))


def promote_margin_labels(lines, normal, outdent=25, window=8):
    """Pull out-dented margin label to start of its block."""
    if not normal or len(lines) < 4:
        return lines
    out = list(lines)
    for i in range(1, len(out)):
        z = out[i]
        if not z[1] or not MARGIN_LABEL.match(z[0].strip()):
            continue
        near = [x for x in out[max(0, i - window):i + window + 1]
                if x[1] and x is not z]
        if len(near) < 4:
            continue
        body = statistics.median([x[1][0] for x in near])
        if z[1][0] > body - outdent:
            continue
        j, last_y = i, None
        while j > 0:
            prev = out[j - 1]
            if not prev[1] or prev[1][0] < body - outdent:
                break
            if last_y is not None and prev[1][3] < last_y - 1.6 * normal:
                break
            if ENUMERATION.match(re.sub(r"\*+", "", prev[0]).lstrip()):
                break
            last_y = prev[1][1]
            j -= 1
        if j < i:
            out.insert(j, out.pop(i))
    return out


def short_lines(lines, window=15, margin_slack=0.08, block_ratio=0.55):
    """Per line: does it end visibly before right margin in justified text?"""
    n = len(lines)
    short, block = [False] * n, [False] * n
    idx = [i for i, z in enumerate(lines) if z[1]]
    for rank, i in enumerate(idx):
        near = [lines[j] for k, j in enumerate(idx) if abs(k - rank) <= window]
        xs = sorted(z[1][2] for z in near)
        margin = statistics.median(xs[-max(3, len(near) // 5):])
        width = margin - min(z[1][0] for z in near)
        if width <= 0:
            continue
        full = sum(1 for z in near if z[1][2] >= margin - 0.02 * width)
        if full < block_ratio * len(near):
            continue
        block[i] = True
        short[i] = lines[i][1][2] < margin - margin_slack * width

    without_coords = [i for i, z in enumerate(lines) if not z[1]]
    if len(without_coords) >= 6:
        med = statistics.median([len(lines[i][0].strip()) for i in without_coords]) or 1
        for i in without_coords:
            short[i] = len(lines[i][0].strip()) < 0.95 * med
    for i in range(n - 1):
        if short[i] and lines[i + 1][0].lstrip("*").lstrip()[:1].islower():
            short[i] = False
    return short, block


def assemble_paragraphs(lines):
    """Resolve hyphens and merge lines into paragraphs."""
    lines = attach_footnote_numbers(lines)
    ys = [z[1][1] for z in lines if z[1]]
    distances = [b - a for a, b in zip(ys, ys[1:]) if 0 < b - a < 200]
    normal = statistics.median(distances) if distances else None
    lines = promote_margin_labels(lines, normal)
    short, block = short_lines(lines)

    out, buffer, last_y, discarded = [], "", None, []
    was_heading, last_marker, prev_idx, buffer_x0 = False, None, None, None
    for i, z in enumerate(lines):
        text, box = z[0], z[1]
        marker = z[2] if len(z) > 2 else None
        if marker == "tabelle":
            if buffer:
                out.append(buffer)
                buffer = ""
            out.append(text)
            was_heading, last_y = False, (box[3] if box else last_y)
            last_marker = marker
            continue
        text = clean_text(text)
        y = box[1] if box else None
        if not text:
            continue
        if is_boilerplate(text, y):
            discarded.append(text)
            continue

        cont = text.lstrip("*")
        hyphen = (buffer.rstrip("*").endswith("-")
                  and not NO_JOIN.match(cont) and cont[:1].islower())

        bare = text.lstrip("*").lstrip()
        heading = is_heading(text, bare)
        continues = (prev_idx is not None and block[prev_idx] and not short[prev_idx]
                     or box and buffer_x0 is not None
                     and box[0] > buffer_x0 + 8
                     or bool(re.search(r"[,;\-–]\**$", buffer)))

        if not buffer or hyphen:
            new_p = False
        elif marker != last_marker:
            new_p = True
        elif (ENUMERATION.match(bare) or KEYWORD.match(bare)
              or (heading and not continues) or was_heading):
            new_p = True
        elif y is not None and last_y is not None and y < last_y - 50:
            new_p = not text[:1].islower()
        elif normal and y is not None and last_y is not None:
            new_p = (y - last_y) > normal * 1.6
        else:
            new_p = bool(re.search(r'[.!?:]["“»)]?\s*$', buffer))

        if buffer and not new_p:
            if hyphen:
                buffer = buffer.rstrip("*")[:-1] + cont
            else:
                buffer = buffer + " " + text
        else:
            if buffer:
                out.append(buffer)
            buffer, buffer_x0 = text, (box[0] if box else None)

        was_heading = ((heading and buffer == text
                        and not (block[i] and not short[i]))
                       or (short[i] and level(buffer) is not None
                           and (len(without_bold(buffer)) <= 90
                                or only_bold(buffer))))
        last_y, last_marker, prev_idx = y, marker, i
    if buffer:
        out.append(buffer)
    out = [balance_bold(re.sub(r"\*\*(\s*)\*\*", r"\1", p)) for p in out]
    assemble_paragraphs.discarded = discarded
    return format_headings(footnotes_obsidian(out))


SENTENCE_END = re.compile(r"[.!?][\"“»)\]]?$")


def format_headings(paragraphs, max_heading=90):
    """Make outline levels visible."""
    out = []
    for p in paragraphs:
        raw = p.lstrip()
        lvl = level(raw)
        if lvl is None or raw[:1] in "|>[":
            out.append(p)
            continue
        blank = without_bold(raw)
        if ((len(blank) <= max_heading or only_bold(raw))
                and (not SENTENCE_END.search(blank) or "**" in raw)):
            out.append("#" * lvl + " " + blank)
        elif not raw.startswith("**"):
            marker, rest = raw.split(None, 1) if " " in raw else (raw, "")
            out.append(f"**{marker}** {rest}" if not marker[:1].isdigit()
                       else p)
        else:
            out.append(p)
    return out


STANDALONE_MARKER = re.compile(
    r"^(?:\*\*)?\s*(?:[-•·▪○●⇒⇨→➢✔o]"
    r"|\(?\d{1,2}[.)]"
    r"|\(?[a-z]{1,3}\)"
    r"|[IVXL]{1,5}\.)\s*(?:\*\*)?$"
)


def merge_fragments(lines, W, gap=0.15, max_pt=60):
    """Merge marker fragment and follow-up text on same baseline."""
    rows = []
    for z in sorted(lines, key=lambda z: z[1][1]):
        y0, y1 = z[1][1], z[1][3]
        if rows:
            r = rows[-1]
            ry0 = min(a[1][1] for a in r)
            ry1 = max(a[1][3] for a in r)
            height = min(y1 - y0, ry1 - ry0) or 1
            if (min(y1, ry1) - max(y0, ry0)) > 0.5 * height:
                r.append(z)
                continue
        rows.append([z])

    out = []
    for r in rows:
        r.sort(key=lambda z: z[1][0])
        i = 0
        while i < len(r):
            text, box = r[i][0], r[i][1]
            while (i + 1 < len(r) and STANDALONE_MARKER.match(text.strip())
                   and r[i + 1][1][0] >= box[2]
                   and r[i + 1][1][0] - box[2] < min(gap * W, max_pt)):
                t2, b2 = r[i + 1][0], r[i + 1][1]
                text = re.sub(r"\*\*(\s*)\*\*", r"\1",
                              text.rstrip() + " " + t2.lstrip())
                box = (box[0], min(box[1], b2[1]), b2[2], max(box[3], b2[3]))
                i += 1
            out.append([text, box])
            i += 1
    return out


def as_callout(paragraphs, title):
    """Put text into a collapsed Obsidian callout."""
    out = [f"> [!note]- {title}"]
    for p in paragraphs:
        out += ["> " + z for z in p.splitlines()] + [">"]
    return "\n".join(out).rstrip("\n>").rstrip()


def page_marker(nr, extra=None):
    """Page marker in grammar format."""
    if extra is not None:
        return f"%% S. {nr} | {extra} %%\n\n"
    return f"%% S. {nr} %%\n\n"


def build_document(frontmatter_text, source_text, blocks_texts):
    """Generate complete preview markdown text."""
    return f"{frontmatter_text}\n{source_text}\n" + "\n\n".join(blocks_texts) + "\n"


def build_frontmatter(title, source_pdf_path, pages, pages_textlayer,
                      pages_ocr, pages_diagram=0, pages_derailed=0,
                      words_suspect=0, words_corrected=0,
                      ocr_model=None, ocr_date=None, ocr_timestamp=None,
                      aborted=None):
    """Build YAML frontmatter for preview file."""
    lines = [
        "---",
        f"titel: {title}",
        f"quelle-pdf: {json.dumps(str(source_pdf_path), ensure_ascii=False)}",
        f"seiten: {pages}",
        f"seiten-textlayer: {pages_textlayer}",
        f"seiten-ocr: {pages_ocr}",
    ]
    if pages_diagram:
        lines.append(f"seiten-diagramm: {pages_diagram}")
    if pages_derailed:
        lines.append(f"seiten-entgleist: {pages_derailed}")
    if words_suspect:
        lines.append(f"woerter-verdaechtig: {words_suspect}")
    if words_corrected:
        lines.append(f"woerter-korrigiert: {words_corrected}")
    if aborted:
        lines.append(f"abgebrochen: {aborted}")
    if ocr_model:
        lines.append(f"ocr-modell: {ocr_model}")
    lines += [
        f"ocr-datum: {ocr_date}",
        f"ocr-zeitpunkt: {ocr_timestamp}",
        "vorschau-format: 1",
        "---",
    ]
    return "\n".join(lines) + "\n"

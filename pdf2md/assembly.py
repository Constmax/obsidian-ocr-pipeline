#!/usr/bin/env python3
"""Markdown assembly: pure functions on recognized lines (Stage 2, Issue #8).

Separated from pdf2md.py so that the layer changing most frequently can be tested
without MLX, fitz, and Vault assets (pdf2md/test). A page block is assembled
by `conversion.page_block()`, which calls `assemble_paragraphs()`; the
helpers behind that are private (Issue #144). The functions sibling modules
import (`parse_lines`, `clean_text`, `is_boilerplate`, …) stay public.
Imports nothing from sibling modules — dependency flows only in this direction:

    pdf2md.py (CLI)  →  conversion.py  →  layout.py, ocr.py, assembly.py

Heavy imports (fitz, numpy, PIL) are function-local in all modules.
"""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, replace
from functools import cached_property

LOC = re.compile(r"<\|LOC_(\d+)\|>")
# --- Post-processing -------------------------------------------------------

# Words that must NOT be joined after a line-end hyphen.
NO_JOIN = re.compile(r"^(und|oder|bzw|sowie|als|wie|bis|von|zu|im|in)\b", re.I)

# Words a phrase cannot end on: text ending in one runs on into the next
# line, whatever the line looks like (Issue #163).
OPEN_END = re.compile(
    r"(?<![\w.])(?:der|die|das|des|dem|den|ein|eine|einer|eines|einem|einen"
    r"|aus|bei|mit|von|vom|zu|zur|zum|für|über|unter|in|im|nach|wegen|gegen"
    r"|durch|ohne|und|oder|bzw\.|sowie|dass|gem\.|vgl\.|i\.\s?V\.\s?m\.)\**$")

# Text that stops mid-sentence: on a lowercase letter (the end of any word,
# capitalised or not), a comma, a semicolon or a hyphen or dash -- not on a
# period, a colon, a digit or a capitalised abbreviation such as "BGB"
# (Issue #163).
MID_SENTENCE = re.compile(r"[a-zäöüß,;\-–]\**$")

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
    re.compile(r"^Klausurenkurs\s*/"),                 # course label header
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
    re.compile(r"^(Lösung|Sachverhalte?|Übersicht)\s*,\s*Seite\s+\d+\s*$", re.I),
    re.compile(r"^Lösung\s*[-–].*Seite\s+\d+\s*$"),          # running head
    re.compile(r"^Klausur\s*Nr\.?\s*\d+\s*[-–]\s*Lösung,\s*Seite\s+\d+\s*$"),
    re.compile(r"^Fall\s*\d*\s*[-–]?\s*L[äöa]?"),      # "Fall 3 - Lä" (truncated)
    re.compile(r"^\s*Seite\s*\d+\s*$", re.I),
]

# --- Page zones ------------------------------------------------------------
# In thousandths of the page height from the top. A recognized line's y is
# its top on this scale. A weaker boilerplate signal needs a zone nearer the
# page edge.
HEADER_ZONE = 70            # zone signals and city lines
FOOTER_ZONE = 950
PAGE_NUMBER_HEADER = 80     # a bare page number
PAGE_NUMBER_FOOTER = 905
# Where assembly_context() looks for running lines in the text layer, by a
# line's centre: the header zone, the zone under the header rule where course
# labels sit (Issue #161), and the footer zone.
RUNNING_HEADER_ZONE = 90
RUNNING_LABEL_ZONE = 120
RUNNING_FOOTER_ZONE = 930
# The bottom of an OCR page where a running footer may come back misread or
# cut at the gutter (Issue #161): the running footer zone, by a line's top.
FOOTER_BAND = RUNNING_FOOTER_ZONE


@dataclass(frozen=True)
class RecognizedLine:
    """One line Stage 2 read from a source page (CONTEXT.md).

    `box` is `(x0, y0, x1, y1)` in thousandths of the page, None when the
    model gave none. `column` counts 0, 1, … in reading order where the
    tile or `split_columns()` knows it, None where not; it is never guessed.
    `container` is `"tabelle"` for a table or `"kasten{i}"` for the box the
    line lies in. Bold stays as `**` in the text.
    """

    text: str
    box: tuple[int, int, int, int] | None = None
    column: int | None = None
    container: str | None = None


def running_text(text):
    """A line as running lines hold it: no bold markers, single spaces."""
    return re.sub(r"\s+", " ", text.replace("*", "")).strip()


@dataclass(frozen=True)
class AssemblyContext:
    """What assembling every page of one document shares: its running lines."""

    running_lines: frozenset[str] = frozenset()
    # The running lines found in the footer zone. Only a footer reaches the
    # footer band, where an OCR page may return it cut at the gutter.
    footer_lines: frozenset[str] = frozenset()

    @cached_property
    def footer_keys(self) -> tuple[str, ...]:
        return tuple(_running_key(line) for line in self.footer_lines)

    def is_running(self, text, y=None, ocr_page=False):
        """Is text a running line? On an OCR page, a line in the footer band
        also counts when it is a misread running footer."""
        if running_text(text) in self.running_lines:
            return True
        return (ocr_page and y is not None and y >= FOOTER_BAND
                and _is_misread_footer(text, self.footer_keys))


@dataclass(frozen=True)
class AssemblyResult:
    """Paragraphs assembled from a page, and the lines it discarded with
    their reason: `running_line`, `page_number` or `boilerplate`."""

    paragraphs: list[str]
    discarded: list[tuple[RecognizedLine, str]]


# Characters OCR confuses in running lines: "26-II", "26-Il", "26-11", "26-|1".
_CONFUSABLE = str.maketrans("il|", "111")
# A gutter piece holds at least this many letters and digits, and this share
# of its footer's, in at least two words.
PIECE_MIN_LENGTH = 5
PIECE_MIN_SHARE = 0.4


def _running_key(text):
    """Letters and digits of text, casefolded, confusable characters as 1."""
    return re.sub(r"[\W_]+", "", text.casefold().translate(_CONFUSABLE))


def _is_misread_footer(text, footer_keys):
    """Is text a running footer read with OCR confusions, or a gutter piece:
    one end of it cut off at the column gutter?

    A scan is read column by column, so a footer spanning both columns comes
    back as two pieces, and the right piece may start with half a glyph (a
    cut through "t" reads "l"). A piece must hold `PIECE_MIN_SHARE` of the
    footer and two words: a city or a subject word alone ("Bremen", "Hessen",
    "Grundfragen") ends a footnote line as well.
    """
    key = _running_key(text)
    if len(key) < PIECE_MIN_LENGTH:
        return False
    if key in footer_keys:
        return True
    if len(re.findall(r"[^\W_]+", text)) < 2:
        return False
    after_half_glyph = key[1:]
    for footer in footer_keys:
        if len(key) > len(footer):
            continue
        min_piece = max(PIECE_MIN_LENGTH, PIECE_MIN_SHARE * len(footer))
        if len(key) >= min_piece and footer.startswith(key):
            return True
        if any(len(end) >= min_piece and footer.endswith(end)
               for end in (key, after_half_glyph)):
            return True
    return False


def is_boilerplate(text, y=None, context=None, ocr_page=False):
    """Detect Hemmer boilerplate."""
    return _boilerplate_reason(text, y, context, ocr_page) is not None


def _boilerplate_reason(text, y=None, context=None, ocr_page=False):
    """Why a line is boilerplate: `running_line`, `page_number` or
    `boilerplate`; None when it is not."""
    t = text.strip().strip("*").strip()
    if not t:
        return None
    if context and context.is_running(t, y, ocr_page):
        return "running_line"
    if any(p.search(t) for p in BOILERPLATE):
        return "boilerplate"
    if t.count(" - ") >= 2 and sum(1 for s in CITIES if s in t) >= 2:
        return "boilerplate"
    if CITY_FRAGMENT.match(t):
        return "boilerplate"

    if len(t) <= 45 and any(p.search(t) for p in ZONE_SIGNALS):
        return "boilerplate"

    if (y is not None
            and (y <= PAGE_NUMBER_HEADER or y >= PAGE_NUMBER_FOOTER)
            and re.fullmatch(r"\d{1,4}", t)):
        return "page_number"

    in_zone = y is not None and (y <= HEADER_ZONE or y >= FOOTER_ZONE)
    if in_zone:
        if any(p.search(t) for p in ZONE_SIGNALS):
            return "boilerplate"
        if len(t) <= 40 and sum(1 for s in CITIES if s in t) >= 1 and "-" in t:
            return "boilerplate"
    return None


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
    # Footnote mark → Obsidian; the $-wrapped form first, or its $ remain
    (re.compile(r"\$\^\{(\d{1,2})\}\$"), r"[^\1]"),
    (re.compile(r"\^\{(\d{1,2})\}"), r"[^\1]"),
]

FN_START = r"[A-ZÄÖÜ„»§(]"
FN_DEF = re.compile(r"^(\d{1,2})\s+(?=" + FN_START + r")(.+)$")
# A definition starts a new sentence: the number follows sentence-ending
# punctuation or a closing bracket/quote. A bare space is not enough —
# citations like "BayVBl. 2016, 77 (78)" would split mid-citation.
FN_DEF_SPLIT = re.compile(r"(?<=[.!?:)\]»\"“”]\s)(?=\d{1,2}\s+" + FN_START + ")")

# A number after a citation word continues the citation ("§ 35 VwVfG",
# "Art. 3 III 1 GG") — it starts no footnote definition and is no footnote
# mark. No roman-numeral alternative: FN_DEF_SPLIT never splits after a bare
# letter, and in the body it would swallow marks glued to "AEUV", "BImSchV".
CITATION_BEFORE = re.compile(
    r"(§+|Art\.|Abs\.|S\.|Satz|Alt\.|Nr\.|Rn\.|Rz\.|Hs\.|Halbs\.|Var\.|"
    r"lit\.|Buchst\.|Seite|Fall|Teil|Rspr\.|Anm\.)\s*$")


def _split_footnote_defs(text):
    """Split a footnote block at definition starts, never inside citations."""
    bounds = [0]
    for m in FN_DEF_SPLIT.finditer(text):
        if CITATION_BEFORE.search(text[:m.start()]):
            continue
        bounds.append(m.start())
    bounds.append(len(text))
    return [text[a:b] for a, b in zip(bounds, bounds[1:])]


def _reference(s, n):
    """First footnote mark n in running text s, or None."""
    for m in re.finditer(rf"(?<=[a-zäöüßA-ZÄÖÜ)\].,;:]){n}(?![\d\]])", s):
        if not CITATION_BEFORE.search(s[:m.start()]):
            return m
    return None


def _merge_definitions(definitions, paragraphs, columns):
    """Definitions `[number, columns, text]` → `({number: text}, strays)`.

    A number stands once on a page (Issue #14). When several definitions
    carry it, the column decides which one keeps it: the column whose
    running text cites the number, else the column that holds a neighbour
    number (n - 1 or n + 1), else the first in reading order. The others are
    citation page numbers taken for footnote numbers; each goes back, number
    included, to the definition before it in its own column. One from the
    winner's column stays under the number. One that opens another column
    has no definition of its own column to go with: it becomes a stray
    paragraph, so its text lands under no other column's number.
    """
    def cites(i):
        n, own, _ = definitions[i]
        return bool(own) and any(own <= c and not p.lstrip().startswith("|")
                                 and _reference(p, n)
                                 for p, c in zip(paragraphs, columns))

    def has_neighbour(i):
        n, own, _ = definitions[i]
        return any(m in (n - 1, n + 1) and c == own for m, c, _ in definitions)

    winner = {}
    for n in dict.fromkeys(d[0] for d in definitions):
        entries = [i for i, d in enumerate(definitions) if d[0] == n]
        column = definitions[entries[0]][1]
        for test in (cites, has_neighbour):
            passing = {definitions[i][1] for i in entries if test(i)}
            if len(passing) == 1:
                column = passing.pop()
                break
        winner[n] = next(i for i in entries if definitions[i][1] == column)

    home, previous = {}, {}
    for i, (n, column, _) in enumerate(definitions):
        if winner[n] == i or column == definitions[winner[n]][1]:
            home[i] = n
        elif column in previous:
            home[i] = home[previous[column]]
        else:
            home[i] = ("stray", i)
        previous[column] = i
    texts = {n: definitions[i][2] for n, i in winner.items()}
    for i, (n, _, text) in enumerate(definitions):
        if winner[n] != i:
            key = home[i]
            texts[key] = (texts[key] + " " if key in texts else "") + f"{n} {text}"
    defs = {k: v for k, v in texts.items() if isinstance(k, int)}
    return defs, [v for k, v in texts.items() if not isinstance(k, int)]


def _footnotes_obsidian(paragraphs, columns=None):
    """Convert footnotes to Obsidian syntax: [^n] in text, [^n]: at block end.

    `columns` gives the set of columns each paragraph covers (empty or None:
    unknown). Definitions are collected with their column and merged
    afterwards by _merge_definitions() (Issue #14). No digit is dropped.
    """
    columns = [frozenset(c or ()) for c in columns] if columns is not None \
        else [frozenset()] * len(paragraphs)
    found, rest, rest_columns = [], [], []   # found: [number, columns, text]
    is_table = lambda p: p.lstrip().startswith("|")
    for p, column in zip(paragraphs, columns):
        if is_table(p):
            rest.append(p)
            rest_columns.append(column)
            continue
        p = re.sub(r"\*\*(.+?)\*\*", r"\1", p) if FN_DEF.match(p.strip("* ")) else p
        m = FN_DEF.match(p.strip())
        if m and int(m.group(1)) <= 99:
            parts = _split_footnote_defs(p.strip())
            detected = False
            for part in parts:
                mm = FN_DEF.match(part.strip())
                if mm:
                    found.append([int(mm.group(1)), column, mm.group(2).strip()])
                    detected = True
            if detected:
                continue
        rest.append(p)
        rest_columns.append(column)

    if not found:
        return rest

    defs, strays = _merge_definitions(found, rest, rest_columns)
    nums = sorted(defs)

    def mark(s):
        for n in nums:
            m = _reference(s, n)
            if m:
                s = s[:m.start()] + f"[^{n}]" + s[m.end():]
        return s
    rest = [p if is_table(p) else mark(p) for p in rest] + strays
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


def _strip_pua(s):
    if not any(PUA_FROM <= c <= PUA_TO for c in s):
        return s
    return "".join(PUA.get(c, "\u25aa") if PUA_FROM <= c <= PUA_TO else c
                   for c in s)


def clean_text(s):
    s = _strip_pua(s)
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
    """One RecognizedLine per output line of the model, box in its tile."""
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
        lines.append(RecognizedLine(plain, box))
    return lines


# Letter labels are one letter or one letter repeated ("a)", "bb)", "aaa)"),
# or a lowercase roman numeral ("iv."). Any other short lowercase word with
# a period is an abbreviation or the end of a sentence ("gem.", "vgl.",
# "hat.", "ff."), and a single letter followed by one is an abbreviation too
# ("i. V.m.", "z. B.", "o. ä.") (Issue #163).
ENUMERATION = re.compile(
    r"^\s*([-•·▪○●⇒⇨→➢✔]"
    r"|\(?\d{1,2}[.)]"
    r"|(?!ff\.)([a-z])\2{0,2}(?:\)|\.(?!\s*[A-Za-zÄÖÜäöü]{1,2}\.))"
    r"|(?:iv|vi{1,3}|ix)[.)]"
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
ABBREVIATION = re.compile(r"^[A-Za-zÄÖÜäöü]{1,2}\.")


def _unbolded(text):
    """The line without any bold markers, as a label check reads it."""
    return re.sub(r"\*+", "", text).lstrip()


def level(text):
    """Outline level (2–6) or None."""
    bare = _unbolded(text)
    for pat, lvl in LEVELS:
        m = pat.match(bare)
        if m:
            return None if ABBREVIATION.match(bare[m.end():].lstrip()) else lvl
    return None


def _without_bold(text):
    return re.sub(r"\*\*", "", text).strip()


def _only_bold(text):
    """Does paragraph consist exclusively of bold spans?"""
    return bool(text.strip()) and not re.sub(r"\*\*.*?\*\*", "", text,
                                             flags=re.S).strip()


def _balance_bold(text):
    """Fix odd count of `**`."""
    if text.count("**") % 2 == 0:
        return text
    i = text.rfind("**")
    return text[:i] + text[i + 2:]


FN_NUMBER = re.compile(r"^\**\s*(\d{1,2})\s*\**$")
FN_TEXT = re.compile(r"^[A-ZÄÖÜ„»§]")


def vertical_overlap(a, b):
    """How far two boxes (x0, y0, x1, y1) overlap in height; < 0: apart."""
    return min(a[3], b[3]) - max(a[1], b[1])


def _attach_footnote_numbers(lines, footer=900, proximity=40):
    """Attach out-dented footnote number at page footer with its text.

    Number and text must share a column: the next line in reading order can
    open the neighbouring column's block (Issue #14). A number set a little
    lower than its text sorts after it; on one row it still belongs to it
    when it stands right before it: a page number further left on the
    running footer's row stays apart.
    """
    def joined(number, z):
        a, b = number.box, z.box
        box = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
        text = f"{FN_NUMBER.match(number.text.strip()).group(1)} {z.text.lstrip()}"
        return replace(z, text=text, box=box)

    def text_of(number, z):
        return (z and z.box and number.column == z.column
                and number.box[0] <= z.box[0]
                and FN_TEXT.match(z.text.lstrip("*").lstrip()))

    out, i = [], 0
    while i < len(lines):
        z = lines[i]
        n = lines[i + 1] if i + 1 < len(lines) else None
        footnote_number = bool(FN_NUMBER.match(z.text.strip()) and z.box
                               and z.box[1] >= footer)
        if (footnote_number and text_of(z, n)
                and abs(n.box[1] - z.box[1]) <= proximity):
            out.append(joined(z, n))
            i += 2
            continue
        p = out[-1] if out else None
        if (footnote_number and text_of(z, p)
                and 0 <= p.box[0] - z.box[2] <= proximity
                and vertical_overlap(z.box, p.box)
                > 0.5 * min(z.box[3] - z.box[1], p.box[3] - p.box[1])):
            out[-1] = joined(z, p)
            i += 1
            continue
        out.append(z)
        i += 1
    return out


def _box_inside(inner, outer, slack=2):
    """Does box inner lie inside box outer, give or take slack units?"""
    return (outer[0] - slack <= inner[0] and inner[2] <= outer[2] + slack
            and outer[1] - slack <= inner[1] and inner[3] <= outer[3] + slack)


def _drop_repeated_labels(lines):
    """Drop an outline label the model read twice.

    The model sometimes returns a line's leading label ("1.") a second time
    as a line of its own, its box inside the box of the line it belongs to
    (Issue #163).
    """
    out = []
    for z in lines:
        prev = out[-1] if out else None
        if (prev is not None and z.box and prev.box
                and STANDALONE_MARKER.match(z.text.strip())
                and _box_inside(z.box, prev.box)):
            label = _without_bold(z.text)
            if _without_bold(prev.text).startswith(label + " "):
                continue
        out.append(z)
    return out


def _is_heading(text, bare):
    """Is this short, entirely bold line a heading?"""
    return (text.startswith("**") and text.endswith("**")
            and text.count("**") == 2 and len(bare) <= 90
            and not MARGIN_LABEL.match(text.strip()))


def _promote_margin_labels(lines, normal, outdent=25, window=8):
    """Pull out-dented margin label to start of its block."""
    if not normal or len(lines) < 4:
        return lines
    out = list(lines)
    for i in range(1, len(out)):
        z = out[i]
        if not z.box or not MARGIN_LABEL.match(z.text.strip()):
            continue
        near = [x for x in out[max(0, i - window):i + window + 1]
                if x.box and x is not z]
        if len(near) < 4:
            continue
        body = statistics.median([x.box[0] for x in near])
        if z.box[0] > body - outdent:
            continue
        j, last_y = i, None
        while j > 0:
            prev = out[j - 1]
            if not prev.box or prev.box[0] < body - outdent:
                break
            if last_y is not None and prev.box[3] < last_y - 1.6 * normal:
                break
            if ENUMERATION.match(_unbolded(prev.text)):
                break
            last_y = prev.box[1]
            j -= 1
        if j < i:
            out.insert(j, out.pop(i))
    return out


def _short_lines(lines, window=15, margin_slack=0.08, block_ratio=0.55):
    """Per line: does it end visibly before right margin in justified text?"""
    n = len(lines)
    short, block = [False] * n, [False] * n
    idx = [i for i, z in enumerate(lines) if z.box]
    for rank, i in enumerate(idx):
        near = [lines[j] for k, j in enumerate(idx) if abs(k - rank) <= window]
        xs = sorted(z.box[2] for z in near)
        margin = statistics.median(xs[-max(3, len(near) // 5):])
        width = margin - min(z.box[0] for z in near)
        if width <= 0:
            continue
        full = sum(1 for z in near if z.box[2] >= margin - 0.02 * width)
        if full < block_ratio * len(near):
            continue
        block[i] = True
        short[i] = lines[i].box[2] < margin - margin_slack * width

    without_coords = [i for i, z in enumerate(lines) if not z.box]
    if len(without_coords) >= 6:
        med = statistics.median([len(lines[i].text.strip()) for i in without_coords]) or 1
        for i in without_coords:
            short[i] = len(lines[i].text.strip()) < 0.95 * med
    for i in range(n - 1):
        if short[i] and lines[i + 1].text.lstrip("*").lstrip()[:1].islower():
            short[i] = False
    return short, block


def assemble_paragraphs(lines, context=None, ocr_page=False):
    """Resolve hyphens and merge recognized lines into paragraphs.

    `ocr_page`: the model read the page, so a running footer may come back
    misread or cut at the gutter, bold comes per recognized line and boxes
    come from ruled lines of the image.

    Where the lines know their column, a column's footnote block is set
    aside when the next column begins, so the running text goes on where it
    stopped instead of inside the last footnote (Issue #14).

    Returns an AssemblyResult — read .paragraphs, not the record itself.
    """
    lines = _drop_repeated_labels(_attach_footnote_numbers(lines))
    ys = [z.box[1] for z in lines if z.box]
    distances = [b - a for a, b in zip(ys, ys[1:]) if 0 < b - a < 200]
    normal = statistics.median(distances) if distances else None
    lines = _promote_margin_labels(lines, normal)
    short, block = _short_lines(lines)

    # out: (paragraph, set of the columns its lines came from)
    out, buffer, last_y, discarded = [], "", None, []
    was_heading, last_marker, prev_idx, buffer_x0 = False, None, None, None
    buffer_columns, last_column, notes = set(), None, []
    for i, z in enumerate(lines):
        text, box, marker, column = z.text, z.box, z.container, z.column
        line_columns = set() if column is None else {column}
        if marker == "tabelle":
            if buffer:
                out.append((buffer, buffer_columns))
                buffer = ""
            out.append((text, line_columns))
            was_heading, last_y = False, (box[3] if box else last_y)
            last_marker = marker
            continue
        text = clean_text(text)
        y = box[1] if box else None
        if not text:
            continue
        reason = _boilerplate_reason(text, y, context, ocr_page)
        if reason is not None:
            discarded.append((replace(z, text=text), reason))
            continue

        if (buffer and column is not None and last_column is not None
                and column != last_column and FN_DEF.match(buffer.strip("* "))):
            # The previous column ends in its footnote block: keep that block
            # for the end and pick up the running text where it stopped.
            held = [(buffer, buffer_columns)]
            while (out and last_column in out[-1][1]
                   and FN_DEF.match(out[-1][0].strip("* "))):
                held.insert(0, out.pop())
            notes += held
            buffer, buffer_columns, buffer_x0 = "", set(), None
            if (out and last_column in out[-1][1]
                    and not out[-1][0].lstrip().startswith("|")):
                buffer, buffer_columns = out.pop()

        cont = text.lstrip("*")
        hyphen = (buffer.rstrip("*").endswith("-")
                  and not NO_JOIN.match(cont) and cont[:1].islower())

        bare = text.lstrip("*").lstrip()
        # A text-layer line bolds per span, so a bold run can close inside
        # the label: "**b)** Text", "**Hinweis**: Text" (Issue #190).
        unbolded = _unbolded(text)
        heading = _is_heading(text, bare)
        continues = (prev_idx is not None and block[prev_idx] and not short[prev_idx]
                     or box and buffer_x0 is not None
                     and box[0] > buffer_x0 + 8
                     or bool(re.search(r"[,;\-–]\**$", buffer)))
        gap_known = bool(normal and y is not None and last_y is not None)
        wide_gap = gap_known and y - last_y > normal * 1.6
        # On a page the model read, bold comes per recognized line and the
        # boxes from ruled lines of the image, so an all-bold line or a box
        # edge can fall inside a sentence (Issue #163). There a line that
        # carries on the buffer's sentence -- it starts lowercase, or the
        # buffer ends on a word no phrase ends on -- is not cut off by a bold
        # line or by the line before it looking like a heading. After a bold
        # heading only the second counts: body text may start with "h.M.".
        # At normal spacing such a line also crosses a box edge, and so does
        # any line after a buffer that stops mid-sentence.
        runs_on = joins_box = False
        if ocr_page:
            runs_on = (bool(OPEN_END.search(buffer))
                       or cont[:1].islower() and not _only_bold(buffer))
            joins_box = (gap_known and not wide_gap
                         and (runs_on or bool(MID_SENTENCE.search(buffer))))

        if not buffer or hyphen:
            new_p = False
        elif marker != last_marker and not joins_box:
            new_p = True
        elif (ENUMERATION.match(unbolded) or KEYWORD.match(unbolded)
              or (heading and not continues and not runs_on)
              or (was_heading and not runs_on)):
            new_p = True
        elif y is not None and last_y is not None and y < last_y - 50:
            new_p = not text[:1].islower()
        elif gap_known:
            new_p = wide_gap
        else:
            new_p = (bool(re.search(r'[.!?:]["“»)]?\s*$', buffer))
                     and not OPEN_END.search(buffer))

        if buffer and not new_p:
            if hyphen:
                buffer = buffer.rstrip("*")[:-1] + cont
            else:
                buffer = buffer + " " + text
            # A paragraph picked up again in the next column covers both.
            buffer_columns = buffer_columns | line_columns
        else:
            if buffer:
                out.append((buffer, buffer_columns))
            buffer, buffer_x0 = text, (box[0] if box else None)
            buffer_columns = line_columns

        # On a model page a bold line can join a bold buffer (a heading
        # wrapped over two lines); the heading still ends there.
        was_heading = ((heading and (buffer == text
                                     or ocr_page and _only_bold(buffer)
                                     and len(_without_bold(buffer)) <= 90)
                        and not (block[i] and not short[i]))
                       or (short[i] and level(buffer) is not None
                           and (len(_without_bold(buffer)) <= 90
                                or _only_bold(buffer))))
        last_y, last_marker, prev_idx, last_column = y, marker, i, column
    if buffer:
        out.append((buffer, buffer_columns))
    out += notes
    paragraphs = _format_headings(_footnotes_obsidian(
        [_balance_bold(re.sub(r"\*\*(\s*)\*\*", r"\1", p)) for p, _ in out],
        [covered for _, covered in out]))
    return AssemblyResult(paragraphs=paragraphs, discarded=discarded)


CLOSER = r"[\"“»)\]]?$"
SENTENCE_END = re.compile(r"[.!?]" + CLOSER)
FINAL_QUESTION = re.compile(r"\?" + CLOSER)
FINAL_LEGAL_FORM = re.compile(r"\be\.\s?V\.$")
NUMBERED_LABEL = re.compile(r"\(?\d")


def _ends_like_body(text):
    """Does a labelled paragraph end like body text? (#164)

    A heading may name a party by its legal form ("gegen den e.V."); any
    other final abbreviation ("nach h.M.", "z. B.") still ends a sentence.
    Under a letter or roman label a heading may be a question ("III.
    Anspruch?"); a numbered question ("1.", "(2)") stays body text: those
    are question lists.
    """
    if FINAL_LEGAL_FORM.search(text):
        return False
    if FINAL_QUESTION.search(text):
        return bool(NUMBERED_LABEL.match(text))
    return bool(SENTENCE_END.search(text))


def _format_headings(paragraphs, max_heading=90):
    """Make outline levels visible."""
    out = []
    for p in paragraphs:
        raw = p.lstrip()
        lvl = level(raw)
        if lvl is None or raw[:1] in "|>[":
            out.append(p)
            continue
        blank = _without_bold(raw)
        if ((len(blank) <= max_heading or _only_bold(raw))
                and (not _ends_like_body(blank) or "**" in raw)):
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
    for z in sorted(lines, key=lambda z: z.box[1]):
        y0, y1 = z.box[1], z.box[3]
        if rows:
            r = rows[-1]
            ry0 = min(a.box[1] for a in r)
            ry1 = max(a.box[3] for a in r)
            height = min(y1 - y0, ry1 - ry0) or 1
            if (min(y1, ry1) - max(y0, ry0)) > 0.5 * height:
                r.append(z)
                continue
        rows.append([z])

    out = []
    for r in rows:
        r.sort(key=lambda z: z.box[0])
        i = 0
        while i < len(r):
            text, box = r[i].text, r[i].box
            while (i + 1 < len(r) and STANDALONE_MARKER.match(text.strip())
                   and r[i + 1].box[0] >= box[2]
                   and r[i + 1].box[0] - box[2] < min(gap * W, max_pt)):
                t2, b2 = r[i + 1].text, r[i + 1].box
                text = re.sub(r"\*\*(\s*)\*\*", r"\1",
                              text.rstrip() + " " + t2.lstrip())
                box = (box[0], min(box[1], b2[1]), b2[2], max(box[3], b2[3]))
                i += 1
            out.append(RecognizedLine(text, box))
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


# --- Reading an existing preview (Issue #106) ------------------------------
#
# Same grammar as `plugin/src/preview-parser.ts` and docs/preview-format.md:
# a marker counts only at line start and outside a code fence.
PREVIEW_MARKER = re.compile(
    r"^%%\s*(?:S\.|p\.|P\.)\s*(\d+)\s*(?:\|(.*?))?\s*%%\s*$", re.I)
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


class PreviewFormatError(ValueError):
    """An existing preview whose pages cannot be told apart."""


@dataclass(frozen=True)
class PreviewPage:
    """One page block of an existing preview, kept verbatim."""
    number: int
    origin: str | None  # textlayer, ocr, diagramm — None for a legacy marker
    text: str           # from the marker line to the end of the block


@dataclass(frozen=True)
class PreviewDocument:
    fields: dict[str, str]
    preamble: str  # between frontmatter and first marker: the Quelle line
    pages: tuple[PreviewPage, ...]


def _marker_origin(extra):
    first = (extra or "").split("|")[0].strip().lower()
    if first in ("diagram", "diagramm"):
        return "diagramm"
    return first if first in ("textlayer", "ocr") else None


def split_preview(text):
    """Split a preview into frontmatter fields, preamble and page blocks.

    Raises PreviewFormatError when there is no frontmatter, no page marker,
    or a page number appears twice: a merge could not say which block a
    page replaces.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise PreviewFormatError("no frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise PreviewFormatError("frontmatter is not closed") from None
    fields = {}
    for line in lines[1:end]:
        key, colon, value = line.partition(":")
        if colon and key.strip():
            fields[key.strip()] = value.strip()

    starts, fence = [], None
    for index in range(end + 1, len(lines)):
        opening = FENCE.match(lines[index])
        if opening:
            mark = opening.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
            continue
        match = None if fence else PREVIEW_MARKER.match(lines[index])
        if match:
            starts.append((index, int(match.group(1)),
                           _marker_origin(match.group(2))))
    if not starts:
        raise PreviewFormatError("no page markers")

    pages, seen = [], set()
    for position, (index, number, origin) in enumerate(starts):
        if number in seen:
            raise PreviewFormatError(f"page {number} appears twice")
        seen.add(number)
        stop = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        block = "\n".join(lines[index:stop]).rstrip()
        pages.append(PreviewPage(number, origin, block))
    preamble = "\n".join(lines[end + 1:starts[0][0]]).strip("\n")
    return PreviewDocument(fields, preamble, tuple(pages))


def build_document(frontmatter_text, source_text, blocks_texts):
    """Generate complete preview markdown text."""
    return f"{frontmatter_text}\n{source_text}\n" + "\n\n".join(blocks_texts) + "\n"


# `vorschau-format` written into every preview; contracts/cli-contract.json
# holds the same number for the plugin's parser (Issue #55).
PREVIEW_FORMAT = 1


def build_frontmatter(title, source_pdf_path, pages, pages_textlayer,
                      pages_ocr, pages_diagram=0, pages_derailed=0,
                      words_suspect=0, words_corrected=0,
                      ocr_model=None, ocr_date=None, ocr_timestamp=None,
                      aborted=None):
    """Build YAML frontmatter for preview file.

    Free text is JSON-quoted, which is valid YAML: a file name such as
    `Fall 8: Anfechtung.pdf` would otherwise break the frontmatter.
    """
    lines = [
        "---",
        f"titel: {json.dumps(str(title), ensure_ascii=False)}",
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
        f"vorschau-format: {PREVIEW_FORMAT}",
        "---",
    ]
    return "\n".join(lines) + "\n"

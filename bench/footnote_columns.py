#!/usr/bin/env python3
"""Footnote blocks on two-column pages (issue #14): test cases and regression.

  source ~/.venvs/mlxocr/bin/activate
  python bench/footnote_columns.py            # the hand-checked pages
  python bench/footnote_columns.py --all      # letter loss on every vector page

Vector pages carry their text exactly, so the text layer is the truth and no
model runs. `footnote_columns_truth.json` lists the pages of the three
solutions named in the issue (2131, 2135, 2143) and, per page, which
footnote numbers stand under the left and under the right column — read off
the out-dented number lines, no page text.

Per page it checks what the issue asks for:

  columns    the text-layer path puts every footnote number into the column
             the truth has it in (the gutter was found at all).
  foreign    definitions that contain words found only in the other
             column's footnote block — a citation under a number of the
             wrong column.
  lost       letters of the text layer that appear neither in the output nor
             among the lines discarded as boilerplate (the residual loss).

A definition may still differ from its footnote for reasons outside this
issue (a citation page number split off as a phantom definition, #99); the
report counts exact matches separately so those stay visible.

`--all` runs the loss count over every vector page in `bench/pages.json`.
Run it on two checkouts to compare them: a page that loses a letter the
other checkout keeps is a regression.
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

from paths import BENCH, VAULT_ROOT as VAULT
import assembly as A
import conversion as C
import layout as L

TRUTH = BENCH / "footnote_columns_truth.json"
GUTTER = 500   # thousandths of the page width; the truth splits there too


def letters(texts):
    """Letter multiset without space, hyphens and Markdown (as regress_steg)."""
    return Counter(re.sub(r"[\s\-–*#>|\[\]^:]+", "", " ".join(texts)))


def words(text):
    text = re.sub(r"-\s+(?=[a-zäöüß])", "", text)
    return Counter(re.findall(r"[A-Za-zÄÖÜäöüß0-9]+", text))


def build(page, context):
    """Text-layer path of pdf2md: lines, columns, assembly result."""
    boxes, _ = L.detect_boxes(page, False,
                              [t[2] for t in L.tables_markdown(page)])
    raw = L.assign_boxes(C.textlayer_lines(page), boxes)
    if hasattr(L, "split_columns_indexed"):
        lines, columns = L.split_columns_indexed(raw)
        result = A.assemble_paragraphs(lines, context, columns=columns)
    else:                                   # a checkout before issue #14
        lines, columns = L.split_columns(raw), None
        result = A.assemble_paragraphs(lines, context)
    return raw, lines, columns, result


def lost(raw, result):
    return sum((letters(line[0] for line in raw) - letters(result.paragraphs)
                - letters(result.discarded)).values())


def blocks(raw, columns):
    """{number: (column, text)} read off the page geometry, not the pipeline.

    `columns` are the truth's number lists. A block runs from its number
    line to the next number of the same column; a line across the gutter
    (the page footer) belongs to no block.
    """
    numbers = {}
    for line in raw:
        m = A.FN_NUMBER.match(line[0].strip())
        if m and line[1] and line[1][1] >= 800:
            numbers.setdefault(int(m.group(1)), line[1])
    column_of = {n: c for c, ns in enumerate(columns) for n in ns}
    gutter = GUTTER
    out = {}
    for n, column in column_of.items():
        if n not in numbers:
            continue
        top = numbers[n][1] - 3
        below = [numbers[m][1] for m in columns[column]
                 if m in numbers and numbers[m][1] > numbers[n][1]]
        bottom = min(below, default=numbers[n][1] + 200) - 3
        inside = (lambda b: b[2] <= gutter) if column == 0 \
            else (lambda b: b[0] >= gutter)
        text = [line[0] for line in raw
                if line[1] and top <= line[1][1] < bottom and inside(line[1])
                and line[1] != numbers[n]
                and not A.FN_NUMBER.match(line[0].strip())]
        out[n] = (column, " ".join(text))
    return out


def open_document(name):
    import fitz
    doc = fitz.open(VAULT / name)
    for page in doc:
        if page.rotation:
            page.remove_rotation()
    return doc, C.assembly_context(doc)


def score():
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))
    totals = Counter()
    for entry in truth["pages"]:
        doc, context = open_document(entry["file"])
        raw, lines, columns, result = build(doc[entry["page"] - 1], context)
        found = blocks(raw, entry["columns"])
        columns_ok = None if columns is None else all(
            {col for line, col in zip(lines, columns)
             if line[0].strip() == str(n) and line[1] and line[1][1] >= 800}
            == {c} for n, (c, _) in found.items())
        defs = {int(m.group(1)): m.group(2) for p in result.paragraphs
                for m in [re.match(r"\[\^(\d+)\]: (.*)", p)] if m}
        foreign = exact = 0
        for number, (column, text) in found.items():
            own = words(text)
            mine = words(" ".join(t for c, t in found.values() if c == column))
            other = words(" ".join(t for c, t in found.values() if c != column))
            got = words(defs.get(number, ""))
            foreign += bool(set(got - own) & (set(other) - set(mine)))
            exact += got == own
        missing = lost(raw, result)
        totals.update(pages=1, columns=bool(columns_ok), notes=len(found),
                      foreign=foreign, exact=exact, lost=missing,
                      lost_pages=bool(missing))
        print(f"  {Path(entry['file']).name[:40]:40s} S.{entry['page']:3d}  "
              f"columns {'n/a' if columns_ok is None else 'ok' if columns_ok else 'WRONG'}  "
              f"foreign {foreign}  exact {exact}/{len(found)}  lost {missing}")
        doc.close()
    print(f"\n{totals['pages']} pages, {totals['notes']} footnotes")
    print(f"  columns right          : {totals['columns']}/{totals['pages']}"
          " (n/a before issue #14: no column assignment)")
    print(f"  definitions with text of the other column: {totals['foreign']}")
    print(f"  definitions exact      : {totals['exact']}/{totals['notes']}")
    print(f"  letters lost           : {totals['lost']} "
          f"on {totals['lost_pages']} pages")


def regress():
    pages = [s for s in json.loads((BENCH / "pages.json").read_text())
             if not s["scanned"]]
    by_file = {}
    for s in pages:
        by_file.setdefault(s["file"], []).append(s["page"])
    checked, losses = 0, []
    for name in sorted(by_file):
        if not (VAULT / name).exists():
            continue
        doc, context = open_document(name)
        for number in sorted(by_file[name]):
            if number > doc.page_count:
                continue
            try:
                raw, _, _, result = build(doc[number - 1], context)
            except Exception as e:  # noqa: BLE001 - one bad page must not stop the run
                print(f"  ERROR {name} S.{number}: {e}")
                continue
            checked += 1
            missing = lost(raw, result)
            if missing:
                losses.append((name, number, missing))
        doc.close()
    print(f"\n{checked} vector pages, letters lost on {len(losses)} "
          f"({sum(m for _, _, m in losses)} letters)")
    for name, number, missing in sorted(losses, key=lambda e: -e[2]):
        print(f"  {Path(name).name[:44]:44s} S.{number:3d}  -{missing}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true",
                        help="letter loss on every vector page of pages.json")
    regress() if parser.parse_args().all else score()

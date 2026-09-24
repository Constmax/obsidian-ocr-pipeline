#!/usr/bin/env python3
"""Structure benchmark for the assembly layer (Issue #20).

Run from the repository root. Source PDFs resolve against VAULT_ROOT; page
images and PDFs stay outside the repository. Vector pages assemble straight
from their text layer, so no model inference is needed at any step.

  python bench/structure_bench.py run     # assemble all truth pages
  python bench/structure_bench.py score   # score candidates vs truth
  python bench/structure_bench.py bless   # bless checked candidates (local)

`run` converts through pdf2md's document runner with ``ocr_adapter=None``:
scan pages would raise there, so the truth set holds vector pages only —
deterministic input, exact fingerprints. `run` populates the page cache from
Issue #11 along the way; a second run reuses it without recomputation.
`score` compares candidates against bench/structure_truth.json, which holds
fingerprints and short anchors but no page text.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pdf2md"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import BENCH, VAULT_ROOT

import conversion as C
import structure as S

TRUTH = BENCH / "structure_truth.json"
RUN_DIR = BENCH / "structure-lauf"
CANDIDATES = RUN_DIR / "candidates.json"


def load_truth():
    return json.loads(TRUTH.read_text(encoding="utf-8"))


def run_candidates():
    """Assemble every truth page through the real pipeline (no model)."""
    truth = load_truth()
    by_file = defaultdict(list)
    for page in truth["pages"]:
        by_file[page["source"]].append(page["page"])
    candidates = {}
    RUN_DIR.mkdir(exist_ok=True)
    for source, numbers in sorted(by_file.items()):
        pdf = VAULT_ROOT / source
        if not pdf.exists():
            print(f"!! missing: {source}")
            continue
        out = RUN_DIR / pdf.stem.replace(" ", "-")
        request = C.ConversionRequest(
            pdf=pdf, output_dir=out,
            selected_pages=frozenset(numbers))
        result = C.convert_document(request, ocr_adapter=None)
        for pres in result.pages:
            key = f"{source}#{pres.number}"
            candidates[key] = {
                "paragraphs": list(pres.paragraphs),
                "discarded": list(pres.discarded),
                "trace": list(pres.trace),
                "diagram": pres.is_diagram,
            }
        print(f"ok  {Path(source).name}: {len(result.pages)} pages "
              f"-> {out.name}")
    CANDIDATES.write_text(json.dumps(candidates, ensure_ascii=False, indent=1)
                          + "\n", encoding="utf-8")
    print(f"-> {CANDIDATES}")
    return candidates


def score_candidates(candidates=None):
    """Score candidates against the blessed truth. Returns exit code."""
    truth = load_truth()
    if candidates is None:
        if not CANDIDATES.exists():
            print("!! no candidates: run `structure_bench.py run` first")
            return 2
        candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    failures, scored = [], 0
    print(f"\n{'id':6s} {'paragraphs':>10s} {'headings':>8s} "
          f"{'footnotes':>9s}  layout")
    for page in truth["pages"]:
        key = f"{page['source']}#{page['page']}"
        if "blocks" not in page:
            print(f"{page['id']:6s} not blessed yet ({key})")
            continue
        if key not in candidates:
            print(f"{page['id']:6s} MISSING candidate ({key})")
            failures.append(page["id"])
            continue
        result = S.compare_structure(
            page["blocks"], candidates[key]["paragraphs"])
        scored += 1
        pars = "ok" if result["paragraphs"] else "DIFF"
        hl = result["heading_levels"]
        heads = ("ok" if hl[0]
                 else f"exp {hl[1]} got {hl[2]}")
        fn = result["footnote_numbers"]
        foots = ("ok" if fn[0] else f"exp {fn[1]} got {fn[2]}")
        flag = "" if result["passed"] else "  <-- FAIL"
        print(f"{page['id']:6s} {pars:>10s} {heads:>8s} "
              f"{foots:>9s}  {page['layout']}{flag}")
        if page.get("note"):
            print(f"        note: {page['note']}")
        if not result["passed"]:
            failures.append(page["id"])
    print(f"\n{scored} pages scored, "
          f"{scored - len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


def bless_candidates(ids=None):
    """Write fingerprints of checked candidates into the truth file."""
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    truth = load_truth()
    wanted = set(ids) if ids else None
    for page in truth["pages"]:
        if wanted is not None and page["id"] not in wanted:
            continue
        key = f"{page['source']}#{page['page']}"
        if key not in candidates:
            print(f"!! no candidate for {page['id']} ({key})")
            continue
        page["blocks"] = S.reference_paragraphs(
            candidates[key]["paragraphs"])
        print(f"blessed {page['id']} "
              f"({len(page['blocks'])} blocks) from {key}")
    TRUTH.write_text(json.dumps(truth, ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8")
    print(f"-> {TRUTH}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "score", "bless"],
                        help="assemble, score, or bless the truth pages")
    parser.add_argument("--ids", nargs="*", default=None,
                        help="bless only these page ids")
    args = parser.parse_args()
    if args.command == "run":
        run_candidates()
        return score_candidates()
    if args.command == "score":
        return score_candidates()
    if args.command == "bless":
        bless_candidates(args.ids)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

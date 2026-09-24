#!/usr/bin/env python3
"""Review helper for structure-truth hand-check (local only, never in CI).

For each truth page: word-coverage of the candidate against the source text
layer (nothing may silently disappear), structural summary, and the full
candidate for reading. Prints to stdout; redirect to a file per page.
"""

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pdf2md"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import BENCH, VAULT_ROOT

import structure as S

CANDIDATES = BENCH / "structure-lauf" / "candidates.json"


def words(text):
    return re.findall(r"[\wÄÖÜäöüß§]+", text)


def main():
    import fitz
    import json

    truth = json.loads((BENCH / "structure_truth.json").read_text())
    candidates = json.loads(CANDIDATES.read_text())
    only = sys.argv[1:] or None
    for page in truth["pages"]:
        if only and page["id"] not in only:
            continue
        key = f"{page['source']}#{page['page']}"
        cand = candidates[key]
        doc = fitz.open(VAULT_ROOT / page["source"])
        src = doc[page["page"] - 1].get_text("text")
        doc.close()
        src_words = Counter(words(src))
        cand_words = Counter(words(" ".join(cand["paragraphs"])))
        missing = src_words - cand_words
        extra = cand_words - src_words
        letters = lambda s: Counter(re.sub(r"[\s\-–*#>|\[\]^]+", "", s))
        src_letters, cand_letters = letters(src), letters(
            " ".join(cand["paragraphs"]))
        lost_letters = src_letters - cand_letters
        gained_letters = cand_letters - src_letters
        parsed = S.parse_paragraphs(cand["paragraphs"])
        print(f"===== {page['id']} {key}  layout={page['layout']}")
        print(f"src words={sum(src_words.values())} "
              f"cand words={sum(cand_words.values())}")
        print(f"LETTERS lost={sum(lost_letters.values())} "
              f"gained={sum(gained_letters.values())}")
        print(f"MISSING from candidate "
              f"({sum(missing.values())}): "
              f"{' '.join(sorted(missing.elements()))[:300]}")
        print(f"EXTRA in candidate "
              f"({sum(extra.values())}): "
              f"{' '.join(sorted(extra.elements()))[:300]}")
        print(f"headings={[(h['level'], h['anchor'][:40]) for h in parsed['headings']]}")
        print(f"footnote defs={sorted(parsed['footnote_defs'])} "
              f"refs={sorted(parsed['footnote_refs'])[:20]} "
              f"tables={parsed['tables']} diagram={cand['diagram']}")
        print(f"discarded={cand['discarded'][:6]}")
        for i, p in enumerate(cand["paragraphs"]):
            print(f"--- [{i}] ---")
            print(p[:900])
        print()


if __name__ == "__main__":
    main()

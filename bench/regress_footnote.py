#!/usr/bin/env python3
"""Regression of citation-safe footnote splitting in `footnotes_obsidian`.

  source ~/.venvs/mlxocr/bin/activate && python bench/regress_footnote.py

Compares the footnote splitter before and after the citation guard
(`split_footnote_defs`: no split inside "§ 35 VwVfG", "Art. 3 III 1 GG")
across all vector pages of the corpus — the text layer is the truth there,
no inference needed. Compared on letter multisets: a citation moved under
a phantom number looks like a word diff but loses no letter; real loss
would be irrecoverable without the original.

Expected: no page loses a character. Changed pages must only merge phantom
definitions back into their real ones.
"""

import json
import re
from pathlib import Path

from paths import BENCH, VAULT_ROOT as VAULT
import conversion as C
import assembly as A
from regress_steg import buchstaben, seite_bauen

OLD_SPLIT = re.compile(r"(?<=[.\s])(?=\d{1,2}\s+" + A.FN_START + ")")


def alt_footnotes(paragraphs):
    """The version before the citation guard: split after any period/space."""
    defs, rest = {}, []
    is_table = lambda p: p.lstrip().startswith("|")
    for p in paragraphs:
        if is_table(p):
            rest.append(p)
            continue
        p = re.sub(r"\*\*(.+?)\*\*", r"\1", p) if A.FN_DEF.match(p.strip("* ")) else p
        m = A.FN_DEF.match(p.strip())
        if m and int(m.group(1)) <= 99:
            detected = False
            for part in OLD_SPLIT.split(p.strip()):
                mm = A.FN_DEF.match(part.strip())
                if mm:
                    k = int(mm.group(1))
                    defs[k] = (defs[k] + " " if k in defs else "") \
                        + mm.group(2).strip()
                    detected = True
            if detected:
                continue
        rest.append(p)
    if defs:
        rest = rest + [""] + [f"[^{k}]: {defs[k]}" for k in sorted(defs)]
    return rest


def main():
    import fitz
    index = BENCH / "pages.json" if (BENCH / "pages.json").exists() \
        else VAULT / "pages.json"
    pages = [s for s in json.loads(index.read_text()) if not s["scanned"]]
    by_file = {}
    for s in pages:
        by_file.setdefault(s["file"], []).append(s["page"])

    real = A.footnotes_obsidian
    n, same, changed, lost = 0, 0, [], []
    for datei in sorted(by_file):
        pfad = VAULT / datei
        if not pfad.exists():
            continue
        doc = fitz.open(pfad)
        for page in doc:
            if page.rotation:
                page.remove_rotation()
        context = A.AssemblyContext(frozenset(C.running_lines(doc)))
        for nr in sorted(by_file[datei]):
            if nr > doc.page_count:
                continue
            page = doc[nr - 1]
            n += 1
            try:
                new = seite_bauen(page, context)
                A.footnotes_obsidian = lambda ps: alt_footnotes(ps)
                old = seite_bauen(page, context)
            except Exception as e:  # noqa: BLE001 - one bad page must not stop the run
                print(f"  ERROR {datei} S.{nr}: {e}")
                continue
            finally:
                A.footnotes_obsidian = real
            if new == old:
                same += 1
                continue
            # Polarity follows regress_steg: letters the NEW code drops.
            # Eliminated phantom definitions leave only their key colons
            # behind ("[^39]:"), which is the repair, not a loss.
            dropped = buchstaben(old) - buchstaben(new)
            minus = sum(v for k, v in dropped.items() if k != ":")
            phantoms = dropped.get(":", 0)
            entry = (datei, nr, minus, phantoms)
            (lost if minus else changed).append(entry)
        doc.close()

    print(f"\n{n} vector pages checked")
    print(f"  unchanged               : {same}")
    print(f"  footnote defs regrouped : {len(changed)}")
    print(f"  phantom key colons gone : "
          f"{sum(p for _, _, _, p in changed)}")
    print(f"  characters LOST         : {len(lost)}")
    for datei, nr, minus, phantoms in changed[:20]:
        print(f"  regrouped {Path(datei).name[:44]:44s} S.{nr:3d}")
    for datei, nr, minus, phantoms in lost[:20]:
        print(f"  LOST {Path(datei).name[:44]:44s} S.{nr:3d}  -{minus} chars")


if __name__ == "__main__":
    main()

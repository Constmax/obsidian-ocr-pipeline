#!/usr/bin/env python3
"""Objektive Auswertung (Phase 4): Normzitat-Treue + Zeichenausbeute.

  python3 .ocr-bench/score.py            # alle Pfade
  python3 .ocr-bench/score.py --pfad A

Normzitat-Treue ist das harte Kriterium: ein verlorenes oder verfaelschtes
'§ 326 V' ist teurer als zwanzig Tippfehler, weil es beim Lernen unauffaellig
bleibt. Verglichen wird gegen den bestehenden Tesseract-Textlayer (Baseline) —
der ist nicht die Wahrheit, aber die Abweichung zeigt, wo hingeschaut werden muss.
"""
import argparse, re, sys
from collections import Counter
from pathlib import Path

BENCH = Path(__file__).resolve().parent

# § 823, §§ 280 I, § 326 V, Art. 4 Abs. 2, § 45 Nr. 2, § 3a I VwVfG
NORM = re.compile(r"(?:§§?|Art\.?)\s*\d+[a-z]?(?:\s*(?:[IVXLC]+|Abs\.?\s*\d+|Nr\.?\s*\d+|S\.?\s*\d+))*")


def norms(text):
    """Normalisierte Normzitate als Multiset."""
    out = []
    for m in NORM.findall(text):
        out.append(re.sub(r"\s+", " ", m).replace("§§", "§").strip())
    return Counter(out)


def report(name, base_txt, out_txt):
    nb, no = norms(base_txt), norms(out_txt)
    fehlend = nb - no          # in Baseline, nicht im Output
    neu = no - nb              # im Output, nicht in Baseline
    gemeinsam = sum((nb & no).values())
    total_b = sum(nb.values())

    lb, lo = len(base_txt.strip()), len(out_txt.strip())
    ratio = lo / lb if lb else 0.0

    treffer = f"{gemeinsam}/{total_b}" if total_b else "—"
    quote = f"{gemeinsam/total_b*100:.0f} %" if total_b else "—"
    print(f"\n\033[1m{name}\033[0m")
    print(f"  Zeichen      : {lo} vs. Baseline {lb}  → Faktor {ratio:.2f}")
    print(f"  Normzitate   : {treffer} wiedergefunden ({quote})")
    if fehlend:
        print(f"  \033[33mfehlend\033[0m      : {', '.join(sorted(fehlend.elements()))[:200]}")
    if neu:
        print(f"  zusaetzlich  : {', '.join(sorted(neu.elements()))[:200]}")

    flags = []
    if total_b >= 3 and gemeinsam / total_b < 0.9:
        flags.append("NORMZITATE UNTER 90 % — K.o.-Kriterium pruefen")
    if ratio < 0.7:
        flags.append("deutlich weniger Text — verschluckter Inhalt?")
    if ratio > 1.4:
        flags.append("deutlich mehr Text — Geistertext/Halluzination?")
    for f in flags:
        print(f"  \033[31m⚠ {f}\033[0m")
    return dict(seite=name, ratio=ratio, norm_quote=(gemeinsam / total_b if total_b else None))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pfad", choices=["A", "B"], help="nur diesen Pfad")
    a = ap.parse_args()

    pfade = [a.pfad] if a.pfad else ["A", "B"]
    any_out = False
    for p in pfade:
        outdir = BENCH / f"out-{p}"
        mds = sorted(outdir.glob("*.md")) if outdir.exists() else []
        if not mds:
            print(f"\n(kein Output in out-{p}/)")
            continue
        any_out = True
        print(f"\n{'='*64}\nPFAD {p}\n{'='*64}")
        for md in mds:
            stem = md.stem.split(".p")[0]
            bl = BENCH / f"{stem}.baseline.txt"
            if not bl.exists():
                # save_to_markdown haengt evtl. Suffixe an
                cand = [b for b in BENCH.glob("*.baseline.txt") if md.stem.startswith(b.name[:5])]
                if not cand:
                    print(f"\n{md.name}: keine Baseline gefunden — uebersprungen")
                    continue
                bl = cand[0]
            report(md.name, bl.read_text(encoding="utf-8"), md.read_text(encoding="utf-8"))

    if not any_out:
        print("\nNoch keine Ergebnisse. Erst run_bench.py laufen lassen.")
        return 1
    print("\nDiagrammseiten (05/06) bewusst NICHT automatisch bewertet — "
          "dort zaehlt die Box-zu-Box-Zuordnung, und die muss man ansehen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

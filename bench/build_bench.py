#!/usr/bin/env python3
"""Erzeugt das Benchmark-Set aus raw/: 6 Seiten als PNG (300 dpi) plus die
Baseline dessen, was die aktuelle Tesseract-Pipeline heute im Textlayer hat.

Liest raw/ ausschliesslich lesend. Braucht nur PyMuPDF (im System-Python da).

  python3 .ocr-bench/build_bench.py
"""
import sys
from pathlib import Path

try:
    import fitz
except ImportError:
    sys.exit("!! PyMuPDF fehlt:  python3 -m pip install pymupdf")

BENCH = Path(__file__).resolve().parent
VAULT = BENCH.parent

# (Name, PDF relativ zum Vault, PDF-Seitennummer, warum diese Seite)
SET = [
    ("01-zweispalter-handschrift",
     "raw/OeR/Verwaltungsrecht-AT/Verwaltungsprozessrecht.pdf", 10,
     "Zweispalter, dichter Kleindruck, Fussnotenblock, handschriftliche "
     "Marginalie, leichte Schraeglage"),
    ("02-zweispalter-dicht",
     "raw/OeR/Verwaltungsrecht-AT/Allgemeines-Verwaltungsrecht-Skript.pdf", 15,
     "Zweispalter, verschachtelte Gliederung (3./a./b.), eingerueckte "
     "Zitatbloecke, Fussnoten"),
    ("03-durchschlag-handschrift",
     "raw/StR/Strafrecht-AT/Strafrecht AT II.pdf", 8,
     "HAERTEFALL: Rueckseiten-Durchschlag als Geistertext, Handschrift, "
     "Ordnerrand, viel Weissraum"),
    ("04-einspaltig-sauber",
     "raw/ZR/Arbeitsrecht/skript-arbeitsrecht-xxl-issa.pdf", 12,
     "Baseline: einspaltig, sauber, Blocksatz, Fettungen, Urteilszitate"),
    ("05-diagramm-sekundaeranspruch",
     "raw/ZR/Schuldrecht-AT/schuldrecht-at-teil-2-sekundaeransprueche-2026.pdf", 2,
     "Diagramm: Anspruchsbaum + 4-Spalten-Zuordnung Ruecktrittsrecht "
     "(Pfeile tragen die Bedeutung)"),
    ("06-diagramm-kausalitaet",
     "raw/ZR/Schuldrecht-AT/schuldrecht-at-teil-5-schadensrecht-2026.pdf", 5,
     "Diagramm: Kausalitaetsbaum, Kaesten mit Fan-Out-Pfeilen"),
]


def main():
    idx = ["# Benchmark-Set — OCR-Kandidatenvergleich", "",
           "Je Seite `<name>.png` (300 dpi, Input fuer die Kandidaten) und",
           "`<name>.baseline.txt` (was die Tesseract-Pipeline heute liefert).", ""]
    missing = 0
    for name, rel, pno, why in SET:
        src = VAULT / rel
        if not src.exists():
            print(f"!! fehlt: {rel}")
            missing += 1
            continue
        doc = fitz.open(src)
        if pno > doc.page_count:
            print(f"!! {rel}: nur {doc.page_count} Seiten, {pno} verlangt")
            doc.close()
            missing += 1
            continue
        page = doc[pno - 1]
        page.get_pixmap(dpi=300).save(BENCH / f"{name}.png")
        baseline = page.get_text("text")
        (BENCH / f"{name}.baseline.txt").write_text(baseline, encoding="utf-8")
        doc.close()

        kb = (BENCH / f"{name}.png").stat().st_size // 1024
        print(f"ok  {name}: {len(baseline):5d} Z. Baseline, {kb} KB PNG")
        idx += [f"## {name}", f"- Quelle: `{rel}` — PDF-Seite {pno}",
                f"- Testet: {why}",
                f"- Baseline (Tesseract heute): {len(baseline)} Zeichen", ""]

    (BENCH / "README.md").write_text("\n".join(idx), encoding="utf-8")
    print(f"\n→ {BENCH}/README.md")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())

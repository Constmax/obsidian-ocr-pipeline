#!/usr/bin/env python3
"""Einzelne Benchmark-Seiten noch einmal — diesmal mit sichtbarem Protokoll.

  source .venv-mlxocr/bin/activate && python .ocr-bench/nachspiel.py 9 23 35 12

bench_ocr.py wirft pdf2mds stdout weg (`stdout=DEVNULL`), damit die Messtabelle
lesbar bleibt. Fuer die Frage, WARUM eine Seite zurueckgegangen ist, ist genau
das die fehlende Information: welche Kachel als entgleist galt, was der
Neuversuch lieferte und ob `_guete` ihn genommen oder verworfen hat.

Argumente sind Seitennummern im Sammel-PDF `bench-lauf/bench-seiten.pdf`.
"""
import json, subprocess, sys
from pathlib import Path

from pfade import BENCH, PDF2MD_PY
from bench_ocr import seiten_trennen, vergleiche


def main():
    import fitz
    nummern = [int(x) for x in sys.argv[1:]] or [9, 23, 35, 12]
    quelle = BENCH / "bench-lauf" / "bench-seiten.pdf"
    arbeit = BENCH / "nachspiel-lauf"
    arbeit.mkdir(exist_ok=True)
    pdf = arbeit / "seiten.pdf"

    herkunft = {h["seite"]: h for h in
                json.loads((BENCH / "bench-lauf" / "herkunft.json").read_text())}

    alt = fitz.open(quelle)
    neu = fitz.open()
    for nr in nummern:
        neu.insert_pdf(alt, from_page=nr - 1, to_page=nr - 1)
    neu.save(pdf)
    neu.close(); alt.close()

    subprocess.run([sys.executable, str(PDF2MD_PY), str(pdf),
                    "--out", str(arbeit / "ocr"),
                    "--bild-dir", str(arbeit / "ocr" / "assets"),
                    "--dpi", "150", "--nur-ocr"], check=True)

    # Wahrheit aus dem grossen Lauf uebernehmen — dort ist sie schon gerechnet
    # und stammt aus demselben Textlayer.
    wahr = seiten_trennen(BENCH / "bench-lauf" / "wahr" / "bench-seiten.md")
    gross = seiten_trennen(BENCH / "bench-lauf" / "ocr" / "bench-seiten.md")
    klein = seiten_trennen(arbeit / "ocr" / "seiten.md")

    print(f"\n{'Datei':32s} {'S.':>4s} | {'gross':>7s} {'einzeln':>8s} | Zeichen")
    for i, nr in enumerate(nummern, start=1):
        if nr not in wahr or i not in klein:
            continue
        a = vergleiche(wahr[nr], gross.get(nr, ""))
        b = vergleiche(wahr[nr], klein[i])
        h = herkunft.get(nr, {})
        print(f"{Path(h.get('datei', '?')).name[:32]:32s} "
              f"{h.get('quellseite', nr):4d} | "
              f"{a['wortgenauigkeit']:7.1%} {b['wortgenauigkeit']:8.1%} | "
              f"{len(wahr[nr]):5d} wahr, {len(gross.get(nr,'')):5d} → "
              f"{len(klein[i]):5d}")


if __name__ == "__main__":
    main()

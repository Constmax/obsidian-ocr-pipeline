#!/usr/bin/env python3
"""Nur die entgleisten Seiten — vorher/nachher, ohne die 34 gesunden mitzurechnen.

  source .venv-mlxocr/bin/activate && python .ocr-bench/bench_defekt.py

Der volle Benchmark braucht 21 min. Fuer die Frage, ob die Reparatur greift,
reichen die sechs Seiten, an denen sie ueberhaupt ansetzt — das ist ein
Bruchteil der Zeit und misst genau den Unterschied.

Gerechnet werden zwei Laeufe auf demselben Sammel-PDF:
  ohne   --neuversuche 0 → Erkennung laeuft, Neuberechnung nicht
  mit    --neuversuche 1 → entgleiste Kachel wird feiner geschnitten
Gemessen wird damit allein die Neuberechnung. Das Kuerzen stehengebliebener
Wiederholungen greift in BEIDEN Laeufen — es haengt an der Erkennung, nicht an
der Neuberechnung. Wer den Abstand zum Stand vor der Aenderung wissen will,
vergleicht die beiden vollen 40-Seiten-Laeufe, nicht diese Spalten.

Die Wahrheit kommt wie im grossen Benchmark aus dem Textlayer derselben Seiten.
"""
import json, subprocess, sys
from pathlib import Path

from pfade import BENCH, PDF2MD_PY
from bench_ocr import seiten_trennen, vergleiche

# Seiten des Sammel-PDF aus bench-lauf/, die im 40-Seiten-Lauf entgleist sind.
DEFEKT = [1, 5, 12, 22, 26, 28]


def main():
    import fitz
    quelle = BENCH / "bench-lauf" / "bench-seiten.pdf"
    arbeit = BENCH / "defekt-lauf"
    arbeit.mkdir(exist_ok=True)
    pdf = arbeit / "defekte-seiten.pdf"

    herkunft = json.loads((BENCH / "bench-lauf" / "herkunft.json").read_text())
    nach_nr = {h["seite"]: h for h in herkunft}

    alt = fitz.open(quelle)
    neu = fitz.open()
    for nr in DEFEKT:
        neu.insert_pdf(alt, from_page=nr - 1, to_page=nr - 1)
    neu.save(pdf)
    neu.close()
    alt.close()
    print(f"{len(DEFEKT)} Seiten → {pdf.name}\n")

    laeufe = {"wahr": [], "ohne": ["--nur-ocr", "--neuversuche", "0"],
              "mit": ["--nur-ocr", "--neuversuche", "1"]}
    for name, zusatz in laeufe.items():
        ziel = arbeit / name
        if (ziel / f"{pdf.stem}.md").exists() and "--neu" not in sys.argv:
            print(f"— Lauf '{name}': vorhanden, uebersprungen")
            continue
        print(f"— Lauf '{name}' …", flush=True)
        subprocess.run([sys.executable, str(PDF2MD_PY), str(pdf),
                        "--out", str(ziel), "--bild-dir", str(ziel / "assets")]
                       + zusatz, check=True)

    wahr = seiten_trennen(arbeit / "wahr" / f"{pdf.stem}.md")
    aus = {n: seiten_trennen(arbeit / n / f"{pdf.stem}.md")
           for n in ("ohne", "mit")}

    print(f"\n{'Datei':30s} {'S.':>4s} | {'Wort ohne':>9s} {'mit':>7s} | "
          f"{'Zitat ohne':>10s} {'mit':>7s} | {'Zeichen':>8s}")
    summe = {"ohne": [0, 0, 0, 0], "mit": [0, 0, 0, 0]}
    for i, nr in enumerate(DEFEKT, start=1):
        if i not in wahr:
            continue
        e = {k: vergleiche(wahr[i], aus[k][i]) for k in ("ohne", "mit")}
        h = nach_nr.get(nr, {})
        for k in ("ohne", "mit"):
            summe[k][0] += e[k]["woerter"]
            summe[k][1] += e[k]["wortgenauigkeit"] * e[k]["woerter"]
            summe[k][2] += e[k]["zitate"]
            summe[k][3] += (e[k]["zitattreue"] or 0) * e[k]["zitate"]
        zt = lambda k: (f"{e[k]['zitattreue']:.0%}"
                        if e[k]["zitattreue"] is not None else "—")
        print(f"{Path(h.get('datei', '?')).name[:30]:30s} "
              f"{h.get('quellseite', nr):4d} | "
              f"{e['ohne']['wortgenauigkeit']:9.1%} "
              f"{e['mit']['wortgenauigkeit']:7.1%} | "
              f"{zt('ohne'):>10s} {zt('mit'):>7s} | "
              f"{len(aus['ohne'][i]):5d}→{len(aus['mit'][i]):5d}")
    print()
    for k in ("ohne", "mit"):
        w, g, z, zg = summe[k]
        print(f"  {k:5s}: Wortgenauigkeit {g/max(w,1):6.1%}   "
              f"Zitattreue {zg/max(z,1):6.1%}  ({z} Zitate)")


if __name__ == "__main__":
    main()

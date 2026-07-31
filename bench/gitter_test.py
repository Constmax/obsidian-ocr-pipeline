#!/usr/bin/env python3
"""Linierte Tabellen in Rasterscans finden — ohne Inferenz.

  source .venv-mlxocr/bin/activate && python .ocr-bench/gitter_test.py [--stichprobe N]

Sucht lange dunkle Geraden im gerenderten Bild. Das ist dieselbe Grundlage, die
PyMuPDF fuer vektorielle Seiten benutzt (strategy="lines_strict") — nur aus
Pixeln statt aus Zeichenbefehlen. Wichtig ist das Tor: keine Linien, keine
Tabelle. Fliesstext faelschlich als Tabelle zu formatieren waere schlimmer, als
eine Tabelle als Fliesstext zu lassen.
"""
import argparse, json, random
from pathlib import Path
import fitz
import numpy as np

VAULT = Path(__file__).resolve().parent.parent


def _laengster_lauf(maske):
    """Je Zeile die Laenge des laengsten zusammenhaengenden True-Laufs.

    Das ist das entscheidende Merkmal. Eine Summe ueber die Zeile zaehlt auch
    Textzeilen mit — eine Textzeile im Zweispalter deckt genauso viel Blattbreite
    ab wie ein Tabellenstrich. Der Unterschied ist die Lueckenlosigkeit: Text hat
    Zwischenraeume zwischen den Buchstaben, ein Strich hat keine.
    """
    n = maske.shape[1]
    lauf = np.zeros(maske.shape[0], dtype=np.int32)
    akt = np.zeros(maske.shape[0], dtype=np.int32)
    for j in range(n):
        akt = np.where(maske[:, j], akt + 1, 0)
        np.maximum(lauf, akt, out=lauf)
    return lauf


def geraden(page, dpi=110, mindest=0.25, dicke=4):
    """(waagerechte_y, senkrechte_x) in normierten 0–1-Koordinaten.

    `mindest` ist der Anteil der Seitenbreite/-hoehe, den ein lueckenloser Lauf
    ueberspannen muss. Ringbindungs-Schatten laeuft am Blattrand senkrecht
    durch, darum werden die aeusseren 4 % verworfen.
    """
    pm = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    a = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width)
    dunkel = a < 170
    H, W = dunkel.shape

    def sammeln(lauf, laenge, rand):
        treffer = np.where(lauf >= laenge * mindest)[0]
        treffer = treffer[(treffer > rand) & (treffer < len(lauf) - rand)]
        aus, block = [], []
        for i in treffer:
            if block and i - block[-1] > dicke:
                aus.append(sum(block) / len(block)); block = []
            block.append(i)
        if block:
            aus.append(sum(block) / len(block))
        return aus

    wa = [y / H for y in sammeln(_laengster_lauf(dunkel), W, int(H * 0.02))]
    se = [x / W for x in sammeln(_laengster_lauf(dunkel.T), H, int(W * 0.04))]
    return wa, se


def hat_tabelle(wa, se):
    """Ein Gitter braucht mindestens 3 Querlinien und 1 innere Laengslinie."""
    return len(wa) >= 3 and len(se) >= 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stichprobe", type=int, default=80)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    seiten = [s for s in json.loads((VAULT / "pages.json").read_text())
              if s["scanned"]]
    random.seed(a.seed)
    probe = random.sample(seiten, min(a.stichprobe, len(seiten)))

    treffer, offen = 0, []
    for s in probe:
        try:
            d = fitz.open(VAULT / s["file"])
            wa, se = geraden(d[s["page"] - 1])
            d.close()
        except Exception as e:
            print(f"  ! {s['file']} S.{s['page']}: {type(e).__name__}")
            continue
        if hat_tabelle(wa, se):
            treffer += 1
            offen.append((s["file"], s["page"], len(wa), len(se)))

    print(f"\n{treffer} von {len(probe)} Scanseiten mit Liniengitter "
          f"({treffer/len(probe):.1%})")
    for f, p, nw, ns in offen[:15]:
        print(f"   {nw:2d} waagerecht, {ns:2d} senkrecht   {f} S.{p}")


if __name__ == "__main__":
    main()
